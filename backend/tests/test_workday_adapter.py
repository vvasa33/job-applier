import os
from pathlib import Path

import pytest

from jobhunter.apply.workday import WorkdayAdapter, WorkdayPageError, parse_workday_document
from jobhunter.browser.errors import SubmitRefused
from jobhunter.browser.manager import BrowserManager
from tests.browser_fixture import FormServer

APPLICATION = Path(__file__).resolve().parent / "fixtures" / "forms" / "workday-application.html"
SEARCH = Path(__file__).resolve().parent / "fixtures" / "forms" / "workday-search.html"
PAGE_URL = "http://127.0.0.1/workday-application.html"


class FakeBrowser:
    def __init__(self, html: str) -> None:
        self.location = PAGE_URL
        self._html = html
        self.calls: list[tuple] = []

    def form_html(self) -> str:
        return self._html

    def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector, value))

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))

    def select(self, selector: str, value: str) -> None:
        self.calls.append(("select", selector, value))

    def upload(self, selector: str, path: Path) -> None:
        self.calls.append(("upload", selector, path))


def _page():
    return parse_workday_document(APPLICATION.read_text(encoding="utf-8"), url=PAGE_URL)


def test_application_page_exposes_fields_without_choosing_answers() -> None:
    page = _page()
    fields = {field.field_id: field for field in page.fields}

    assert page.title == "My Information"
    assert page.heading == "My Information"
    assert page.url == PAGE_URL
    assert list(fields) == [
        "legalName--firstName",
        "email",
        "country",
        "degree",
        "workAuthorization",
        "previouslyEmployed",
        "file-upload-input-ref",
        "question-123",
        "hear",
        "mystery",
    ]

    name = fields["legalName--firstName"]
    assert name.label == "First Name"
    assert name.type == "text"
    assert name.required is True
    assert name.current_value == "Ada"
    assert name.options == ()
    assert name.confidence == 0.95
    assert name.selector == "#first-name"

    assert fields["email"].type == "text"
    assert fields["email"].label == "Email"
    assert fields["email"].required is False
    assert fields["email"].current_value == ""

    country = fields["country"]
    assert country.type == "dropdown"
    assert country.label == "Country"
    assert country.required is True
    assert country.options == ("United States", "Canada")
    assert country.current_value == ""
    assert country.confidence == 0.95

    degree = fields["degree"]
    assert degree.type == "dropdown"
    assert degree.options == ("Bachelors", "Masters")
    assert degree.current_value == "Masters"
    assert degree.option_selectors == ()

    authorization = fields["workAuthorization"]
    assert authorization.type == "radio"
    assert authorization.label == "Are you authorized to work in the United States?"
    assert authorization.options == ("Yes", "No")
    assert authorization.current_value == "No"
    assert authorization.required is True

    assert fields["previouslyEmployed"].type == "checkbox"
    assert fields["previouslyEmployed"].label == "I have previously worked here"
    assert fields["previouslyEmployed"].current_value == ""

    resume = fields["file-upload-input-ref"]
    assert resume.type == "file"
    assert resume.label == "Resume/CV"
    assert resume.selector == "#resume-file"

    question = fields["question-123"]
    assert question.type == "free_text"
    assert question.label == "Why do you want this internship?"
    assert question.required is True
    assert question.confidence == 0.95

    assert fields["hear"].type == "free_text"
    assert fields["hear"].label == "How did you hear about us?"
    assert fields["hear"].confidence == 0.7

    assert fields["mystery"].label == ""
    assert fields["mystery"].type == "text"
    assert fields["mystery"].confidence == 0.4

    assert [(button.button_id, button.kind) for button in page.navigation] == [
        ("pageFooterBackButton", "back"),
        ("pageFooterNextButton", "next"),
        ("pageFooterSubmitButton", "submit"),
    ]


def test_a_job_search_page_is_not_an_application() -> None:
    with pytest.raises(WorkdayPageError, match="not a Workday application page"):
        parse_workday_document(SEARCH.read_text(encoding="utf-8"))


def test_requested_actions_use_the_detected_controls_and_never_submit() -> None:
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    adapter = WorkdayAdapter(browser)

    adapter.fill("email", "ada@example.com")
    adapter.choose("degree", "Bachelors")
    adapter.choose("country", "United States")
    adapter.choose("workAuthorization", "Yes")
    adapter.upload("file-upload-input-ref", Path("resume.txt"))

    assert browser.calls == [
        ("fill", "#email", "ada@example.com"),
        ("select", "#degree", "Bachelors"),
        ("click", "#country"),
        ("click", "#country-us"),
        ("click", "#auth-yes"),
        ("upload", "#resume-file", Path("resume.txt")),
    ]
    with pytest.raises(WorkdayPageError, match="no option"):
        adapter.choose("country", "Mexico")
    with pytest.raises(SubmitRefused, match="refusing to submit"):
        adapter.press("pageFooterSubmitButton")
    assert ("click", "#submit") not in browser.calls
    assert all(call[0] != "submit" for call in browser.calls)


def test_live_page_actions_do_not_submit(tmp_path) -> None:
    pytest.importorskip("playwright")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("visible Chromium needs a display")
    server = FormServer()
    server.start()
    resume = tmp_path / "resume.txt"
    resume.write_text("Ada Lovelace\n", encoding="utf-8")
    manager = BrowserManager(profile_dir=tmp_path / "profile")
    try:
        manager.open()
        manager.navigate(server.page("workday-application.html"), expect="workday-application.html")
        adapter = WorkdayAdapter(manager)
        assert adapter.read_page().field("legalName--firstName").current_value == "Ada"

        adapter.fill("legalName--firstName", "Grace Hopper")
        adapter.choose("country", "United States")
        adapter.choose("workAuthorization", "Yes")
        adapter.set_checked("previouslyEmployed", True)
        adapter.upload("file-upload-input-ref", resume)
        adapter.press("pageFooterNextButton")

        page = adapter.read_page()
        assert page.field("legalName--firstName").current_value == "Grace Hopper"
        assert page.field("country").current_value == "United States"
        assert page.field("workAuthorization").current_value == "Yes"
        assert page.field("previouslyEmployed").current_value == "yes"
        assert "next" in manager.read_structure().notices
        with pytest.raises(SubmitRefused, match="refusing to submit"):
            adapter.press("pageFooterSubmitButton")
        assert "submitted" not in manager.read_structure().notices
    finally:
        manager.close()
        server.stop()
