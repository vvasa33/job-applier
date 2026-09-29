import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobhunter.api.app import create_app
from jobhunter.apply.decisions import ApplicantData, DecisionSource, KnownFact
from jobhunter.apply.runner import refuse_submit, resume_page, run_page
from jobhunter.applications import change_status, open_application
from jobhunter.browser.errors import BrowserError, NavigationTimeout, SubmitRefused
from jobhunter.browser.manager import BrowserManager
from jobhunter.db.models import Application, ApplicationEvent
from jobhunter.db.records import JobSighting, add_master_resume, add_resume_version, record_job_sighting
from jobhunter.domain.enums import ApplicationStatus, EventActor, ResumeVersionKind
from jobhunter.domain.errors import ApplicationTransitionError
from tests.browser_fixture import FormServer

APPLICATION = Path(__file__).resolve().parent / "fixtures" / "forms" / "workday-application.html"
SEARCH = Path(__file__).resolve().parent / "fixtures" / "forms" / "workday-search.html"
APPLY_URL = "http://jobs.example/northwind/apply"


class FakeBrowser:
    def __init__(self, html: str, *, fail: str | None = None) -> None:
        self.location = "about:blank"
        self._html = html
        self.fail = fail
        self.calls: list[tuple] = []

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


def _ready(session, url: str = APPLY_URL) -> Application:
    job = record_job_sighting(
        session,
        JobSighting(
            title="Software Engineering Intern",
            company="Northwind",
            url=url,
            ats_type="workday",
            external_id=url.rsplit("/", 1)[-1],
            description_text="Build services in Python.",
        ),
    )
    application = open_application(session, job, reason="Worth applying.")
    resume = add_master_resume(session, path="/tmp/master.tex", sha256=f"master-{application.id}")
    version = add_resume_version(
        session,
        resume=resume,
        kind=ResumeVersionKind.tailored,
        application=application,
        sha256=f"tailored-{application.id}",
        tex_path=f"/tmp/applications/{application.id}/resume.tex",
        pdf_path=f"/tmp/applications/{application.id}/resume.pdf",
    )
    for target, resume_id in (
        (ApplicationStatus.matched, None),
        (ApplicationStatus.saved, None),
        (ApplicationStatus.tailoring, None),
        (ApplicationStatus.ready_to_apply, version.id),
    ):
        change_status(
            session,
            application,
            to=target,
            actor=EventActor.user,
            reason=f"Moved to {target.value}.",
            resume_version_id=resume_id,
        )
    session.flush()
    return application


def _facts() -> ApplicantData:
    return ApplicantData(
        facts=[
            KnownFact(key="contact.first_name", value="Ada", confidence=1, source=DecisionSource.profile),
            KnownFact(key="contact.email", value="ada@example.com", confidence=1, source=DecisionSource.profile),
        ],
        resume_text="Built a compiler.",
    )


def _events(session, application: Application) -> list[ApplicationEvent]:
    from sqlalchemy import select

    return list(
        session.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(ApplicationEvent.id)
        ).all()
    )


def test_auto_fill_is_entered_and_everything_else_pauses(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    run_page(db_session, application, browser, _facts())

    assert application.status is ApplicationStatus.waiting_for_user
    assert ("navigate", APPLY_URL) in browser.calls
    assert ("fill", "#email", "ada@example.com") in browser.calls
    assert ("click", "#country") not in browser.calls
    assert ("click", "#auth-yes") not in browser.calls
    assert ("click", "#auth-no") not in browser.calls
    assert ("click", "#submit") not in browser.calls
    assert ("fill", "#why", "Built a compiler.") not in browser.calls

    kinds = [event.event_type for event in _events(db_session, application)]
    assert "page_opened" in kinds
    assert "field_filled" in kinds
    assert "suggestion_created" in kinds
    assert "input_required" in kinds
    assert "page_inspected" in kinds
    assert "waiting_for_user" in kinds
    suggestion = next(event for event in _events(db_session, application) if event.event_type == "suggestion_created")
    assert suggestion.data["proposed_value"] is None
    assert "compiler" not in suggestion.data["reasoning"]
    assert application.started_at is not None


def test_a_search_page_or_timeout_waits_instead_of_guessing(db_session) -> None:
    search = _ready(db_session, "http://jobs.example/search")
    browser = FakeBrowser(SEARCH.read_text(encoding="utf-8"))
    run_page(db_session, search, browser, _facts())
    assert search.status is ApplicationStatus.waiting_for_user
    assert [call[0] for call in browser.calls] == ["navigate"]
    assert any(event.event_type == "waiting_for_user" and "not a Workday" in event.data["reason"] for event in _events(db_session, search))

    timed_out = _ready(db_session, "http://jobs.example/slow")
    failing = FakeBrowser(APPLICATION.read_text(encoding="utf-8"), fail="navigate")
    run_page(db_session, timed_out, failing, _facts())
    assert timed_out.status is ApplicationStatus.waiting_for_user
    assert failing.calls == []


def test_a_fill_failure_stops_the_page(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"), fail="fill")
    run_page(db_session, application, browser, _facts())
    assert application.status is ApplicationStatus.waiting_for_user
    assert ("click", "#country") not in browser.calls
    assert ("click", "#submit") not in browser.calls


def test_resume_enters_only_the_provided_answers_and_still_refuses_to_submit(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    run_page(db_session, application, browser, _facts())
    browser.calls.clear()
    resume_page(
        db_session,
        application,
        browser,
        _facts(),
        {
            "country": "United States",
            "workAuthorization": "No",
            "previouslyEmployed": "no",
            "question-123": "I want the compiler work on the posting.",
        },
    )

    assert application.status is ApplicationStatus.applying
    assert ("fill", "#why", "I want the compiler work on the posting.") in browser.calls
    assert ("click", "#country") in browser.calls
    assert ("click", "#country-us") in browser.calls
    assert ("click", "#submit") not in browser.calls
    with pytest.raises(SubmitRefused, match="Autonomous submission is not enabled"):
        refuse_submit(db_session, application)
    assert any(event.event_type == "submit_blocked" for event in _events(db_session, application))
    assert ("click", "#submit") not in browser.calls


def test_submit_is_refused_while_required_fields_are_open(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    run_page(db_session, application, browser, _facts())
    with pytest.raises(SubmitRefused, match="not resolved"):
        refuse_submit(db_session, application)
    assert application.status is ApplicationStatus.waiting_for_user
    assert ("click", "#submit") not in browser.calls


def test_applying_cannot_start_before_the_application_is_ready(db_session) -> None:
    job = record_job_sighting(
        db_session,
        JobSighting(
            title="Software Engineering Intern",
            company="Northwind",
            url=APPLY_URL,
            ats_type="workday",
            external_id="early",
        ),
    )
    application = open_application(db_session, job)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    with pytest.raises(ApplicationTransitionError, match="ready to apply"):
        run_page(db_session, application, browser, _facts())
    assert application.status is ApplicationStatus.found
    assert browser.calls == []


def test_an_attestation_is_filled_only_after_the_user_answers(db_session) -> None:
    html = """
    <div data-automation-id="applyFlowPage">
      <div data-automation-id="formField-agree">
        <label for="agree">I certify that this is true</label>
        <input id="agree" type="checkbox" data-automation-id="agree">
      </div>
    </div>
    """
    application = _ready(db_session, "http://jobs.example/attest")
    browser = FakeBrowser(html)
    run_page(db_session, application, browser, ApplicantData())
    assert application.status is ApplicationStatus.waiting_for_user
    assert ("click", "#agree") not in browser.calls

    resume_page(db_session, application, browser, ApplicantData(), {"agree": "yes"})
    assert ("click", "#agree") in browser.calls
    assert application.status is ApplicationStatus.applying
    with pytest.raises(SubmitRefused, match="not enabled"):
        refuse_submit(db_session, application)


def test_api_runs_against_the_provided_browser_and_returns_pending_fields(settings) -> None:
    app = create_app(settings)
    html = APPLICATION.read_text(encoding="utf-8")
    browser = FakeBrowser(html)
    app.state.browser_factory = lambda: browser
    session = app.state.session_factory()
    try:
        application = _ready(session, APPLY_URL)
        session.commit()
        application_id = application.id
    finally:
        session.close()

    client = TestClient(app)
    started = client.post(
        f"/api/applications/{application_id}/run",
        json={"facts": [{"key": "contact.email", "value": "ada@example.com", "confidence": 1, "source": "profile"}]},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "waiting_for_user"
    assert any(field["field_id"] == "workAuthorization" for field in body["pending_fields"])
    assert ("fill", "#email", "ada@example.com") in browser.calls

    blocked = client.post(f"/api/applications/{application_id}/submit")
    assert blocked.status_code == 409
    assert "not resolved" in blocked.json()["detail"]
    assert ("click", "#submit") not in browser.calls


def test_visible_browser_pauses_without_submitting(settings, tmp_path) -> None:
    pytest.importorskip("playwright")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("visible Chromium needs a display")
    server = FormServer()
    server.start()
    manager = BrowserManager(profile_dir=tmp_path / "profile")
    try:
        manager.open()
        app = create_app(settings)
        db = app.state.session_factory()
        try:
            url = server.page("workday-application.html")
            application = _ready(db, url)
            run_page(db, application, manager, _facts())
            db.commit()
            assert application.status is ApplicationStatus.waiting_for_user
            assert "submitted" not in manager.read_structure().notices
            page = manager.form_html()
            assert "ada@example.com" in page
        finally:
            db.close()
    finally:
        manager.close()
        server.stop()
