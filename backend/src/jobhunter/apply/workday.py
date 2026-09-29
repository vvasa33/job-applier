"""Read a Workday application page and perform only the browser action that was requested."""

from html.parser import HTMLParser
from pathlib import Path

from jobhunter.apply.fields import ApplicationField, NavigationButton, WorkdayPage
from jobhunter.browser.errors import SubmitRefused
from jobhunter.browser.manager import BrowserManager

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})
_PLACEHOLDERS = frozenset({"", "select", "select one", "choose", "choose one"})
_TEXT_TYPES = frozenset({"", "text", "email", "tel", "url", "search", "password", "number"})


class WorkdayPageError(Exception):
    """The open page is not a Workday application, or it has no such field."""


class _Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs = {key: "" if value is None else value for key, value in attrs}
        self.children: list[_Node] = []
        self.text_parts: list[str] = []

    def get(self, name: str) -> str:
        return self.attrs.get(name, "")

    def text(self) -> str:
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text())
        return " ".join(" ".join(parts).split())


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", [])
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, attrs)
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].text_parts.append(data)


class WorkdayAdapter:
    """Turns one open Workday application page into fields, then runs requested actions."""

    def __init__(self, browser: BrowserManager) -> None:
        self._browser = browser

    def read_page(self) -> WorkdayPage:
        return parse_workday_document(self._browser.form_html(), url=self._browser.location)

    def fill(self, field_id: str, value: str) -> None:
        field = self._field(field_id, {"text", "free_text"})
        self._browser.fill(field.selector, value)

    def choose(self, field_id: str, option: str) -> None:
        field = self._field(field_id, {"dropdown", "radio"})
        if option not in field.options:
            raise WorkdayPageError(f"{field_id} has no option {option!r}")
        if field.type == "radio":
            self._browser.click(dict(field.option_selectors)[option])
            return
        if field.option_selectors:
            self._browser.click(field.selector)
            self._browser.click(dict(field.option_selectors)[option])
            return
        self._browser.select(field.selector, option)

    def set_checked(self, field_id: str, checked: bool) -> None:
        field = self._field(field_id, {"checkbox"})
        is_checked = field.current_value == "yes"
        if is_checked != checked:
            self._browser.click(field.selector)

    def upload(self, field_id: str, path: Path) -> None:
        field = self._field(field_id, {"file"})
        self._browser.upload(field.selector, path)

    def press(self, button_id: str) -> None:
        page = self.read_page()
        try:
            button = page.button(button_id)
        except KeyError as exc:
            raise WorkdayPageError(f"this page has no navigation button {button_id}") from exc
        if button.kind == "submit":
            raise SubmitRefused(f"refusing to submit the application: {button.label or button_id}")
        self._browser.click(button.selector)

    def _field(self, field_id: str, types: set[str]) -> ApplicationField:
        page = self.read_page()
        try:
            field = page.field(field_id)
        except KeyError as exc:
            raise WorkdayPageError(f"this page has no field {field_id}") from exc
        if field.type not in types:
            raise WorkdayPageError(f"{field_id} is a {field.type} field")
        return field


def parse_workday_document(html: str, *, url: str = "") -> WorkdayPage:
    builder = _Builder()
    builder.feed(html)
    root = builder.root
    if not any(_auto(node) == "applyFlowPage" or _auto(node).startswith("formField-") for node in _walk(root)):
        raise WorkdayPageError("this page is not a Workday application page")
    fields: list[ApplicationField] = []
    for container in _leaf_fields(root):
        fields.extend(_fields_in(root, container))
    title = next((node.text() for node in _walk(root) if node.tag == "title"), "")
    heading = _heading(root)
    return WorkdayPage(
        url=url,
        title=title,
        heading=heading,
        fields=tuple(fields),
        navigation=tuple(_navigation(root)),
    )


def _fields_in(root: _Node, container: _Node) -> list[ApplicationField]:
    controls = _controls(container)
    radios = [node for node in controls if node.tag == "input" and node.get("type") == "radio"]
    groups = [node for node in controls if node.get("role") == "radiogroup"]
    others = [node for node in controls if node not in radios and node.get("role") != "radiogroup"]
    found: list[ApplicationField] = []
    if radios:
        found.append(_radio_field(root, container, groups[0] if groups else container, radios))
    found.extend(_control_field(root, container, control) for control in others)
    return found


def _control_field(root: _Node, container: _Node, control: _Node) -> ApplicationField:
    label, required = _label_and_required(root, container, control)
    field_type, options, value, option_selectors, certain = _describe_control(container, control, label)
    return _field(
        control=control,
        container=container,
        label=label,
        field_type=field_type,
        options=options,
        required=required or control.get("aria-required") == "true",
        value=value,
        certain=certain,
        option_selectors=option_selectors,
    )


def _radio_field(root: _Node, container: _Node, group: _Node, radios: list[_Node]) -> ApplicationField:
    label_node = _labelledby(root, group) or _group_label(container, radios)
    raw = label_node.text() if label_node is not None else ""
    label = _clean(raw)
    options: list[str] = []
    selectors: list[tuple[str, str]] = []
    selected = ""
    for radio in radios:
        option = _option_label(container, radio)
        options.append(option)
        selectors.append((option, _selector(radio)))
        if "checked" in radio.attrs:
            selected = option
    required = "*" in raw or group.get("aria-required") == "true" or any(
        radio.get("aria-required") == "true" for radio in radios
    )
    return _field(
        control=group if group.get("data-automation-id") else radios[0],
        container=container,
        label=label,
        field_type="radio",
        options=tuple(options),
        required=required,
        value=selected,
        certain=True,
        option_selectors=tuple(selectors),
    )


def _describe_control(
    container: _Node, control: _Node, label: str
) -> tuple[str, tuple[str, ...], str, tuple[tuple[str, str], ...], bool]:
    if control.tag == "textarea":
        return "free_text", (), control.text() or control.get("value"), (), True
    if control.tag == "select":
        options, selected = _select_options(control)
        return "dropdown", options, selected, (), True
    if control.tag == "button" and control.get("aria-haspopup") == "listbox":
        options = tuple(
            (node.text(), _selector(node))
            for node in _walk(container)
            if node.get("role") == "option" and node.text()
        )
        current = control.text()
        value = "" if current.casefold() in _PLACEHOLDERS else current
        return "dropdown", tuple(option for option, _selector_ in options), value, options, True
    input_type = control.get("type").casefold()
    if input_type == "file":
        return "file", (), "", (), True
    if input_type == "checkbox":
        return "checkbox", (), "yes" if "checked" in control.attrs else "", (), True
    if label.endswith("?") or "question" in _auto(control):
        return "free_text", (), control.get("value"), (), False
    return "text", (), control.get("value"), (), input_type in _TEXT_TYPES


def _field(
    *,
    control: _Node,
    container: _Node,
    label: str,
    field_type: str,
    options: tuple[str, ...],
    required: bool,
    value: str,
    certain: bool,
    option_selectors: tuple[tuple[str, str], ...],
) -> ApplicationField:
    raw_id = _auto(control) or _auto(container) or control.get("id")
    field_id = raw_id.removeprefix("formField-")
    if label and certain:
        confidence = 0.95
    elif label:
        confidence = 0.7
    else:
        confidence = 0.4
    return ApplicationField(
        field_id=field_id,
        label=label,
        type=field_type,
        options=options,
        required=required,
        current_value=value,
        confidence=confidence,
        selector=_selector(control if control.tag != "div" else _primary_selector_node(control, container)),
        option_selectors=option_selectors,
    )


def _primary_selector_node(group: _Node, container: _Node) -> _Node:
    if group.get("id") or group.get("data-automation-id"):
        return group
    return container


def _label_and_required(root: _Node, container: _Node, control: _Node) -> tuple[str, bool]:
    label_node = _labelledby(root, control) or _label_for(container, control.get("id")) or _first_bare_label(container)
    raw = label_node.text() if label_node is not None else ""
    return _clean(raw), "*" in raw


def _select_options(control: _Node) -> tuple[tuple[str, ...], str]:
    options: list[str] = []
    selected = ""
    for node in _walk(control):
        if node is control or node.tag != "option":
            continue
        text = node.text()
        choice = node.get("value") if "value" in node.attrs else text
        if not choice or choice.casefold() in _PLACEHOLDERS or text.casefold() in _PLACEHOLDERS:
            continue
        options.append(choice)
        if "selected" in node.attrs:
            selected = choice
    return tuple(options), selected


def _navigation(root: _Node) -> list[NavigationButton]:
    buttons: list[NavigationButton] = []
    for node in _walk(root):
        if node.tag != "button" or node.get("aria-haspopup") == "listbox" or _inside_field(node, root):
            continue
        label = node.text()
        kind = _navigation_kind(_auto(node), label)
        if kind is None:
            continue
        buttons.append(
            NavigationButton(
                button_id=_auto(node) or node.get("id") or kind,
                label=label,
                kind=kind,
                selector=_selector(node),
            )
        )
    return buttons


def _navigation_kind(automation_id: str, label: str) -> str | None:
    blob = f"{automation_id} {label}".casefold()
    if "submit" in blob:
        return "submit"
    if "back" in blob:
        return "back"
    if "next" in blob or "continue" in blob:
        return "next"
    return None


def _controls(container: _Node) -> list[_Node]:
    found: list[_Node] = []
    for node in _walk(container):
        if node is container:
            continue
        if node.tag == "input" and node.get("type").casefold() == "hidden":
            continue
        if node.tag in {"input", "textarea", "select"}:
            found.append(node)
        elif node.tag == "button" and node.get("aria-haspopup") == "listbox":
            found.append(node)
        elif node.get("role") == "radiogroup":
            found.append(node)
    return found


def _leaf_fields(root: _Node) -> list[_Node]:
    containers = [node for node in _walk(root) if _auto(node).startswith("formField-")]
    leaves: list[_Node] = []
    for container in containers:
        nested = [
            node
            for node in _walk(container)
            if node is not container and _auto(node).startswith("formField-")
        ]
        if not nested:
            leaves.append(container)
    return leaves


def _heading(root: _Node) -> str:
    for node in _walk(root):
        if _auto(node) == "pageHeaderTitle" or node.tag in {"h1", "h2"}:
            text = node.text()
            if text:
                return text
    return ""


def _labelledby(root: _Node, control: _Node) -> _Node | None:
    target = control.get("aria-labelledby").split()
    if not target:
        return None
    return _by_id(root, target[0])


def _label_for(container: _Node, control_id: str) -> _Node | None:
    if not control_id:
        return None
    for node in _walk(container):
        if node.tag == "label" and node.get("for") == control_id:
            return node
    return None


def _first_bare_label(container: _Node) -> _Node | None:
    for node in _walk(container):
        if node.tag == "label" and not node.get("for"):
            return node
    return None


def _group_label(container: _Node, radios: list[_Node]) -> _Node | None:
    option_ids = {radio.get("id") for radio in radios if radio.get("id")}
    for node in _walk(container):
        if node.tag == "label" and node.get("for") not in option_ids:
            return node
    return None


def _option_label(container: _Node, radio: _Node) -> str:
    label = _label_for(container, radio.get("id"))
    if label is not None and label.text():
        return _clean(label.text())
    return radio.get("value")


def _by_id(root: _Node, node_id: str) -> _Node | None:
    for node in _walk(root):
        if node.get("id") == node_id:
            return node
    return None


def _inside_field(node: _Node, root: _Node) -> bool:
    parents = {id(parent) for parent in _ancestors(node, root)}
    return any(id(field) in parents for field in _leaf_fields(root))


def _ancestors(node: _Node, root: _Node) -> list[_Node]:
    found: list[_Node] = []

    def visit(current: _Node, chain: list[_Node]) -> bool:
        if current is node:
            found.extend(chain)
            return True
        for child in current.children:
            if visit(child, [*chain, current]):
                return True
        return False

    visit(root, [])
    return found


def _selector(node: _Node) -> str:
    if node.get("id"):
        return f"#{node.get('id')}"
    automation_id = _auto(node)
    if automation_id:
        return f'[data-automation-id="{automation_id}"]'
    return node.tag


def _auto(node: _Node) -> str:
    return node.get("data-automation-id")


def _clean(text: str) -> str:
    return " ".join(text.replace("*", " ").split())


def _walk(node: _Node):
    yield node
    for child in node.children:
        yield from _walk(child)
