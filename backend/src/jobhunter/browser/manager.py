"""Drive one visible Chromium window. Ordinary actions refuse to submit; only `submit` with an authorization can."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from jobhunter.browser.errors import BrowserError, NavigationTimeout, SubmitRefused, UnexpectedPage

_STRUCTURE = """() => {
  const selectorFor = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const tag = el.tagName.toLowerCase();
    const name = el.getAttribute("name");
    const value = el.getAttribute("value");
    if (name && value) return `${tag}[name="${name}"][value="${value}"]`;
    if (name) return `${tag}[name="${name}"]`;
    return tag;
  };
  const controls = [...document.querySelectorAll("input, select, textarea, button")].map((el) => {
    const type = (el.getAttribute("type") || "").toLowerCase();
    const label = el.labels && el.labels[0] ? el.labels[0].textContent.trim() : null;
    let value = "";
    if (type === "password") value = "";
    else if (type === "file") value = el.files && el.files[0] ? el.files[0].name : "";
    else value = el.value || "";
    const checkable = type === "checkbox" || type === "radio";
    return {
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      type,
      name: el.getAttribute("name"),
      label,
      value,
      options: el.tagName === "SELECT" ? [...el.options].map((option) => option.value) : [],
      checked: checkable ? el.checked : null,
      disabled: el.disabled,
    };
  });
  return {
    url: location.href,
    title: document.title,
    headings: [...document.querySelectorAll("h1, h2, h3")].map((heading) => heading.textContent.trim()),
    notices: [...document.querySelectorAll("[data-notice]")].map((node) => node.textContent.trim()),
    controls,
  };
}"""

_FORM_HTML = """() => {
  const sync = (el) => {
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (type === "checkbox" || type === "radio") {
      if (el.checked) el.setAttribute("checked", "checked");
      else el.removeAttribute("checked");
      return;
    }
    if (el.tagName === "SELECT") {
      for (const option of el.options) {
        if (option.selected) option.setAttribute("selected", "selected");
        else option.removeAttribute("selected");
      }
      return;
    }
    if (type === "file") return;
    if (el.tagName === "TEXTAREA") {
      el.textContent = el.value;
      return;
    }
    el.setAttribute("value", el.value);
  };
  document.querySelectorAll("input, textarea, select").forEach(sync);
  return document.documentElement.outerHTML;
}"""

_SUBMITS = """(el) => {
  const type = (el.getAttribute("type") || "").toLowerCase();
  if (el.tagName === "INPUT" && (type === "submit" || type === "image")) return true;
  if (el.tagName === "BUTTON") {
    const resolved = type || "submit";
    return resolved === "submit" && (el.form !== null || el.hasAttribute("form"));
  }
  return false;
}"""


class SubmitAuthorization:
    """Permission to press one submit control once. Only the application submit gate creates these."""

    def __init__(self, reference: str, reason: str) -> None:
        self.reference = reference
        self.reason = reason
        self._used = False

    @property
    def used(self) -> bool:
        return self._used

    def consume(self) -> None:
        if self._used:
            raise SubmitRefused(f"the submit authorization for {self.reference} was already used")
        self._used = True


@dataclass(frozen=True)
class ActionRecord:
    sequence: int
    action: str
    target: str
    detail: str
    url: str
    ok: bool
    error: str | None
    at: str


@dataclass(frozen=True)
class PageControl:
    selector: str
    tag: str
    type: str
    name: str | None
    label: str | None
    value: str
    options: tuple[str, ...]
    checked: bool | None
    disabled: bool


@dataclass(frozen=True)
class PageStructure:
    url: str
    title: str
    headings: tuple[str, ...]
    notices: tuple[str, ...]
    controls: tuple[PageControl, ...]

    def control(self, selector: str) -> PageControl:
        for item in self.controls:
            if item.selector == selector:
                return item
        raise BrowserError(f"page has no control {selector}")


class BrowserManager:
    """One persistent, visible Chromium profile. There is no headless mode."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        record_path: Path | None = None,
        navigation_timeout_ms: int = 15000,
        action_timeout_ms: int = 5000,
    ) -> None:
        if navigation_timeout_ms < 1 or action_timeout_ms < 1:
            raise BrowserError("timeouts must be positive")
        self.profile_dir = Path(profile_dir)
        self.record_path = Path(record_path) if record_path is not None else self.profile_dir / "jobhunter-actions.jsonl"
        self.navigation_timeout_ms = navigation_timeout_ms
        self.action_timeout_ms = action_timeout_ms
        self.actions: list[ActionRecord] = []
        self._sequence: int | None = None
        self._expect: str | None = None
        self._playwright = None
        self._context = None
        self._page = None

    def __enter__(self) -> "BrowserManager":
        self.open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def open(self) -> None:
        if self._context is not None:
            return
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.chmod(0o700)
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=False,
                viewport={"width": 1280, "height": 800},
                accept_downloads=False,
                args=["--window-size=1280,800"],
            )
        except Exception as exc:
            self.close()
            hint = ""
            if "Executable doesn't exist" in str(exc):
                hint = " Install it with `playwright install chromium`."
            raise BrowserError(f"Chromium could not start: {exc}.{hint}") from exc
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_navigation_timeout(self.navigation_timeout_ms)
        self._page.set_default_timeout(self.action_timeout_ms)
        self._record("open", str(self.profile_dir), "visible chromium", ok=True, error=None)

    def close(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = None
        self._page = None
        self._playwright = None
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()

    def navigate(self, url: str, *, expect: str | None = None, timeout_ms: int | None = None) -> None:
        timeout = self.navigation_timeout_ms if timeout_ms is None else timeout_ms

        def operation() -> None:
            self._require_page()
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            except PlaywrightTimeout as exc:
                raise NavigationTimeout(f"navigation to {url} timed out after {timeout} ms") from exc
            if expect is not None and not url_matches(self._page.url, expect):
                raise UnexpectedPage(f"expected the page to match {expect!r} but landed on {self._page.url}")
            self._expect = expect

        self._guard("navigate", url, expect or "", operation)

    def click(self, selector: str) -> None:
        def operation() -> None:
            self._refuse_submit(selector)
            try:
                self._page.click(selector)
            except PlaywrightTimeout as exc:
                raise BrowserError(f"click timed out for {selector}") from exc
            self._check_expected(selector)

        self._guard("click", selector, "", operation)

    def fill(self, selector: str, value: str) -> None:
        def operation() -> None:
            self._refuse_submit(selector)
            if self._input_type(selector) == "file":
                raise BrowserError(f"{selector} is a file input; use upload")
            try:
                self._page.fill(selector, value)
            except PlaywrightTimeout as exc:
                raise BrowserError(f"fill timed out for {selector}") from exc

        self._guard("fill", selector, value[:300], operation)

    def select(self, selector: str, value: str) -> None:
        def operation() -> None:
            if self._tag(selector) != "select":
                raise BrowserError(f"{selector} is not a dropdown")
            try:
                self._page.select_option(selector, value)
            except PlaywrightTimeout as exc:
                raise BrowserError(f"select timed out for {selector}") from exc

        self._guard("select", selector, value, operation)

    def upload(self, selector: str, path: Path) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            raise BrowserError(f"upload file does not exist: {file_path}")

        def operation() -> None:
            if self._input_type(selector) != "file":
                raise BrowserError(f"{selector} is not a file input")
            try:
                self._page.set_input_files(selector, str(file_path))
            except PlaywrightTimeout as exc:
                raise BrowserError(f"upload timed out for {selector}") from exc

        self._guard("upload", selector, file_path.name, operation)

    def screenshot(self, path: Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        def operation() -> Path:
            self._require_page()
            self._page.screenshot(path=str(destination), full_page=True)
            return destination

        return self._guard("screenshot", str(destination), "", operation)

    def read_structure(self) -> PageStructure:
        def operation() -> PageStructure:
            self._require_page()
            raw = self._page.evaluate(_STRUCTURE)
            controls = tuple(
                PageControl(
                    selector=item["selector"],
                    tag=item["tag"],
                    type=item["type"],
                    name=item["name"],
                    label=item["label"],
                    value=item["value"],
                    options=tuple(item["options"]),
                    checked=item["checked"],
                    disabled=item["disabled"],
                )
                for item in raw["controls"]
            )
            return PageStructure(
                url=raw["url"],
                title=raw["title"],
                headings=tuple(raw["headings"]),
                notices=tuple(raw["notices"]),
                controls=controls,
            )

        return self._guard("read", "", "page structure", operation)

    def submit(self, selector: str, authorization: SubmitAuthorization) -> None:
        """Press a submit control. The authorization is consumed even if the click fails."""

        if not isinstance(authorization, SubmitAuthorization):
            raise SubmitRefused("a submit needs an authorization from the submit gate")
        authorization.consume()

        def operation() -> None:
            self._require_page()
            try:
                self._page.click(selector)
            except PlaywrightTimeout as exc:
                raise BrowserError(f"submit timed out for {selector}") from exc

        self._guard("submit", selector, authorization.reference, operation)

    def settle(self, timeout_ms: int = 3000) -> None:
        """Give the page a moment to finish loading after an action. A slow page is not an error."""

        self._require_page()
        try:
            self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except (PlaywrightTimeout, PlaywrightError):
            pass

    @property
    def location(self) -> str:
        self._require_page()
        return self._page.url

    def form_html(self) -> str:
        """Serialize the live page, including the current values of form controls."""

        def operation() -> str:
            self._require_page()
            return self._page.evaluate(_FORM_HTML)

        return self._guard("read", "", "form html", operation)

    def _refuse_submit(self, selector: str) -> None:
        self._require_page()
        try:
            submits = self._page.eval_on_selector(selector, _SUBMITS)
        except PlaywrightTimeout as exc:
            raise BrowserError(f"control was not found: {selector}") from exc
        if submits:
            raise SubmitRefused(f"refusing to activate a control that submits the form: {selector}")

    def _tag(self, selector: str) -> str:
        self._require_page()
        try:
            return self._page.eval_on_selector(selector, "el => el.tagName.toLowerCase()")
        except PlaywrightTimeout as exc:
            raise BrowserError(f"control was not found: {selector}") from exc

    def _input_type(self, selector: str) -> str:
        self._require_page()
        try:
            return self._page.eval_on_selector(selector, "el => (el.getAttribute('type') || '').toLowerCase()")
        except PlaywrightTimeout as exc:
            raise BrowserError(f"control was not found: {selector}") from exc

    def _check_expected(self, selector: str) -> None:
        if self._expect is not None and not url_matches(self._page.url, self._expect):
            raise UnexpectedPage(
                f"after activating {selector}, expected {self._expect!r} but the page is {self._page.url}"
            )

    def _require_page(self) -> None:
        if self._page is None:
            raise BrowserError("the browser is not open")

    def _guard(self, action: str, target: str, detail: str, operation):
        try:
            result = operation()
        except BrowserError as exc:
            self._record(action, target, detail, ok=False, error=str(exc))
            raise
        except PlaywrightError as exc:
            message = str(exc).splitlines()[0]
            error = BrowserError(f"{action} failed for {target or 'the page'}: {message}")
            self._record(action, target, detail, ok=False, error=str(error))
            raise error from exc
        self._record(action, target, detail, ok=True, error=None)
        return result

    def _record(self, action: str, target: str, detail: str, *, ok: bool, error: str | None) -> None:
        if self._sequence is None:
            self._sequence = _existing_lines(self.record_path)
        self._sequence += 1
        url = "" if self._page is None else self._page.url
        record = ActionRecord(
            sequence=self._sequence,
            action=action,
            target=target,
            detail=detail,
            url=url,
            ok=ok,
            error=error,
            at=datetime.now(timezone.utc).isoformat(),
        )
        self.actions.append(record)
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        with self.record_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            handle.flush()


def url_matches(url: str, expect: str) -> bool:
    path = url.split("?", 1)[0]
    if any(char in expect for char in "*?["):
        return fnmatch(url, expect) or fnmatch(path, expect)
    return expect in url


def _existing_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())
