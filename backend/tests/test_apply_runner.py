import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobhunter.api.app import create_app
from jobhunter.apply.decisions import ApplicantData, DecisionSource, KnownFact
from jobhunter.apply.runner import page_context, pending_fields, refuse_submit, respond, resume_page, run_page, waiting_applications
from jobhunter.applications import change_status, open_application
from jobhunter.browser.errors import BrowserError, NavigationTimeout, SubmitRefused
from jobhunter.browser.manager import BrowserManager
from jobhunter.db.models import Application, ApplicationAnswer, ApplicationEvent
from jobhunter.db.records import JobSighting, add_master_resume, add_resume_version, record_job_sighting
from jobhunter.domain.enums import ApplicationStatus, EventActor, ResumeVersionKind, ValueSource
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

    def screenshot(self, path: Path) -> Path:
        self.calls.append(("screenshot", str(path)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path


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


def test_pause_keeps_a_screenshot_and_the_page_address(db_session, tmp_path) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    shots = tmp_path / "screenshots"
    run_page(db_session, application, browser, _facts(), screenshot_dir=shots)

    paused = next(event for event in _events(db_session, application) if event.event_type == "waiting_for_user")
    assert paused.data["url"] == APPLY_URL
    assert paused.data["screenshot"] == f"application-{application.id}.png"
    assert (shots / paused.data["screenshot"]).is_file()
    context = page_context(db_session, application, shots)
    assert context["has_screenshot"] is True
    assert context["page_url"] == APPLY_URL
    question = pending_fields(db_session, application)[0]
    assert "confidence" in question
    assert "reasoning" in question


def test_approve_records_the_suggestion_and_continues(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    draft = "I want the compiler work."
    applicant = ApplicantData(
        facts=[
            *_facts().facts,
            KnownFact(key="essay.why", value=draft, confidence=1, source=DecisionSource.answer_bank),
        ]
    )
    run_page(db_session, application, browser, applicant)
    browser.calls.clear()
    respond(db_session, application, browser, applicant, action="approve", field_id="question-123")

    assert ("fill", "#why", draft) in browser.calls
    assert ("click", "#submit") not in browser.calls
    answer = _answer(db_session, application, "question-123")
    assert answer.value == draft
    assert answer.value_source is ValueSource.llm_draft_approved
    filled = next(
        event
        for event in _events(db_session, application)
        if event.event_type == "field_filled" and event.data.get("field_id") == "question-123"
    )
    assert filled.data["origin"] == "suggested"
    assert application.status is ApplicationStatus.waiting_for_user


def test_edit_records_a_user_answer(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    run_page(db_session, application, browser, _facts())
    respond(
        db_session,
        application,
        browser,
        _facts(),
        action="edit",
        field_id="country",
        value="United States",
    )

    assert ("click", "#country-us") in browser.calls
    answer = _answer(db_session, application, "country")
    assert answer.value == "United States"
    assert answer.value_source is ValueSource.human
    filled = next(
        event
        for event in _events(db_session, application)
        if event.event_type == "field_filled" and event.data.get("field_id") == "country"
    )
    assert filled.data["origin"] == "user"


def test_skip_leaves_the_question_blank_and_moves_on(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    run_page(db_session, application, browser, _facts())
    browser.calls.clear()
    respond(db_session, application, browser, _facts(), action="skip", field_id="country")

    assert ("click", "#country") not in browser.calls
    assert ("click", "#submit") not in browser.calls
    assert all(field["field_id"] != "country" for field in pending_fields(db_session, application))
    assert any(event.event_type == "answer_skipped" for event in _events(db_session, application))
    assert application.status is ApplicationStatus.waiting_for_user


def test_stop_withdraws_without_touching_the_browser(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    run_page(db_session, application, browser, _facts())
    browser.calls.clear()
    respond(db_session, application, None, _facts(), action="stop")

    assert browser.calls == []
    assert application.status is ApplicationStatus.withdrawn
    assert waiting_applications(db_session) == []


def test_approve_without_a_suggestion_is_rejected(db_session) -> None:
    application = _ready(db_session)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
    run_page(db_session, application, browser, _facts())
    browser.calls.clear()
    with pytest.raises(ApplicationTransitionError, match="no suggested answer"):
        respond(db_session, application, browser, _facts(), action="approve", field_id="country")
    assert browser.calls == []
    assert application.status is ApplicationStatus.waiting_for_user


def test_a_skipped_required_field_still_blocks_submit(db_session) -> None:
    html = """
    <div data-automation-id="applyFlowPage">
      <div data-automation-id="formField-agree">
        <label for="agree">I certify that this is true</label>
        <input id="agree" type="checkbox" data-automation-id="agree" aria-required="true">
      </div>
    </div>
    """
    application = _ready(db_session, "http://jobs.example/attest-skip")
    browser = FakeBrowser(html)
    run_page(db_session, application, browser, ApplicantData())
    respond(db_session, application, browser, ApplicantData(), action="skip", field_id="agree")
    assert ("click", "#agree") not in browser.calls
    assert application.status is ApplicationStatus.applying
    with pytest.raises(SubmitRefused, match="not resolved"):
        refuse_submit(db_session, application)


def test_the_queue_lists_only_applications_waiting_for_input(db_session) -> None:
    waiting = _ready(db_session, "http://jobs.example/waiting")
    ready = _ready(db_session, "http://jobs.example/ready")
    run_page(db_session, waiting, FakeBrowser(APPLICATION.read_text(encoding="utf-8")), _facts())

    queued = waiting_applications(db_session)
    assert [item.id for item in queued] == [waiting.id]
    assert ready.status is ApplicationStatus.ready_to_apply


def test_api_review_queue_serves_the_page_image_and_records_the_decision(settings) -> None:
    app = create_app(settings)
    browser = FakeBrowser(APPLICATION.read_text(encoding="utf-8"))
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
        json={
            "facts": [
                {"key": "contact.email", "value": "ada@example.com", "confidence": 1, "source": "profile"},
                {"key": "essay.why", "value": "I want the compiler work.", "confidence": 1, "source": "answer_bank"},
            ]
        },
    )
    assert started.status_code == 200
    body = started.json()
    assert body["company"] == "Northwind"
    assert body["title"] == "Software Engineering Intern"
    assert body["has_screenshot"] is True
    essay = next(field for field in body["pending_fields"] if field["field_id"] == "question-123")
    assert essay["proposed_value"] == "I want the compiler work."
    assert essay["confidence"] > 0

    queue = client.get("/api/review")
    assert queue.status_code == 200
    assert queue.json()[0]["application_id"] == application_id
    assert queue.json()[0]["company"] == "Northwind"

    image = client.get(f"/api/applications/{application_id}/screenshot")
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG")

    approved = client.post(
        f"/api/applications/{application_id}/review",
        json={"action": "approve", "field_id": "question-123"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "waiting_for_user"
    assert all(field["field_id"] != "question-123" for field in approved.json()["pending_fields"])
    assert ("fill", "#why", "I want the compiler work.") in browser.calls
    assert ("click", "#submit") not in browser.calls

    stopped = client.post(
        f"/api/applications/{application_id}/review",
        json={"action": "stop"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "withdrawn"
    assert stopped.json()["waiting_reason"] is None
    assert stopped.json()["has_screenshot"] is False
    assert client.get("/api/review").json() == []


def _answer(session, application: Application, field_key: str) -> ApplicationAnswer:
    from sqlalchemy import select

    answer = session.scalar(
        select(ApplicationAnswer).where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.field_key == field_key,
        )
    )
    assert answer is not None
    return answer
