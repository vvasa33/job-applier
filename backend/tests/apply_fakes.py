"""Fake browsers for the apply runner and the agent. They never reach a real site."""

from pathlib import Path

from jobhunter.browser.errors import BrowserError, NavigationTimeout
from jobhunter.browser.manager import SubmitAuthorization

FORMS = Path(__file__).resolve().parent / "fixtures" / "forms"
FLOW = ("flow-info.html", "flow-questions.html", "flow-review.html")
CONFIRMATION = "<h2>Thank you for applying</h2><p>Your application was submitted.</p>"


class FakeBrowser:
    def __init__(self, html: str, *, fail: str | None = None) -> None:
        self.location = "about:blank"
        self._html = html
        self.fail = fail
        self.calls: list[tuple] = []
        self.closed = False

    def navigate(self, url: str, expect: str | None = None, timeout_ms: int | None = None) -> None:
        if self.fail == "navigate":
            raise NavigationTimeout("navigation timed out")
        self.calls.append(("navigate", url))
        self.location = url

    def form_html(self) -> str:
        return self._html

    def fill(self, selector: str, value: str) -> None:
        if self.fail == "fill":
            raise BrowserError("fill failed")
        self.calls.append(("fill", selector, value))

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))

    def select(self, selector: str, value: str) -> None:
        self.calls.append(("select", selector, value))

    def upload(self, selector: str, path: Path) -> None:
        self.calls.append(("upload", selector, path))

    def submit(self, selector: str, authorization: SubmitAuthorization) -> None:
        authorization.consume()
        self.calls.append(("submit", selector))

    def screenshot(self, path: Path) -> Path:
        self.calls.append(("screenshot", str(path)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path

    def close(self) -> None:
        self.closed = True

    @property
    def submits(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "submit"]


class FlowBrowser(FakeBrowser):
    """Walks the local flow fixtures. Next advances only when the page's required choice was made."""

    def __init__(self, *, pages: tuple[str, ...] = FLOW, on_submit: str = "confirm", fail: str | None = None) -> None:
        super().__init__("", fail=fail)
        self.pages = pages
        self.on_submit = on_submit
        self._index = 0
        self._chosen: set[str] = set()
        self._confirmed = False

    def navigate(self, url: str, expect: str | None = None, timeout_ms: int | None = None) -> None:
        super().navigate(url, expect, timeout_ms)
        self._index = 0
        self._chosen.clear()
        self._confirmed = False

    def form_html(self) -> str:
        if self.fail == "read":
            raise BrowserError("the page could not be read")
        if self._confirmed:
            return CONFIRMATION
        return (FORMS / self.pages[self._index]).read_text(encoding="utf-8")

    def click(self, selector: str) -> None:
        super().click(selector)
        if selector.startswith("#auth-"):
            self._chosen.add("auth")
        if selector == "#next":
            if self.pages[self._index] == "flow-questions.html" and "auth" not in self._chosen:
                return
            self._index = min(self._index + 1, len(self.pages) - 1)

    def submit(self, selector: str, authorization: SubmitAuthorization) -> None:
        super().submit(selector, authorization)
        if self.on_submit == "error":
            raise BrowserError("the submit click timed out")
        if self.on_submit == "confirm":
            self._confirmed = True
