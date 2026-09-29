import os

import pytest

from jobhunter.api.app import create_app
from jobhunter.apply.decisions import ApplicantData, DecisionSource, KnownFact
from jobhunter.apply.runner import (
    page_context,
    recover_interrupted_submit,
    respond,
    resume_ready,
    run_page,
)
from jobhunter.applications import change_status
from jobhunter.apply.submission import SubmitPolicy, confirmation_text
from jobhunter.browser.errors import SubmitRefused
from jobhunter.browser.manager import BrowserManager, SubmitAuthorization
from jobhunter.db.records import record_application_event
from jobhunter.domain.enums import ApplicationStatus, AutonomyLevel, EventActor
from jobhunter.domain.errors import ApplicationTransitionError
from jobhunter.domain.time import utcnow
from tests.apply_fakes import FlowBrowser
from tests.browser_fixture import FormServer
from tests.test_apply_runner import _events, _facts, _ready

FLOW_URL = "http://jobs.example/flow/apply"


def _kinds(session, application) -> list[str]:
    return [event.event_type for event in _events(session, application)]


def _to_review(session, browser: FlowBrowser, url: str = FLOW_URL, policy: SubmitPolicy | None = None):
    application = _ready(session, url)
    run_page(session, application, browser, _facts(), policy=policy)
    assert page_context(session, application, None)["pause_kind"] == "questions"
    respond(
        session,
        application,
        browser,
        _facts(),
        action="edit",
        field_id="workAuthorization",
        value="Yes",
        policy=policy,
    )
    return application


def test_assist_mode_waits_for_confirmation_then_submits_once(db_session) -> None:
    browser = FlowBrowser()
    application = _to_review(db_session, browser)

    assert application.status is ApplicationStatus.waiting_for_user
    assert page_context(db_session, application, None)["pause_kind"] == "confirm_submit"
    assert browser.submits == []
    assert "submit_ready" in _kinds(db_session, application)

    respond(db_session, application, browser, _facts(), action="confirm_submit")

    assert application.status is ApplicationStatus.submitted
    assert browser.submits == [("submit", "#submit")]
    assert application.submit_intent_at is not None
    assert "Thank you for applying" in application.confirmation_text
    kinds = _kinds(db_session, application)
    assert kinds.index("submit_intent") < kinds.index("submitted")
    submitted = next(event for event in _events(db_session, application) if event.event_type == "submitted")
    assert submitted.data["automatic"] is False


def test_changing_an_answer_after_confirming_needs_a_new_confirmation(db_session) -> None:
    browser = FlowBrowser()
    application = _to_review(db_session, browser)
    ready = next(event for event in _events(db_session, application) if event.event_type == "submit_ready")
    record_application_event(
        db_session,
        application,
        event_type="submit_confirmed",
        actor=EventActor.user,
        data={"reason": "Confirmed an older set of answers.", "fingerprint": "stale" + ready.data["fingerprint"]},
    )
    run_page(db_session, application, browser, _facts())
    assert browser.submits == []
    assert page_context(db_session, application, None)["pause_kind"] == "confirm_submit"


def test_observe_mode_never_submits(db_session) -> None:
    browser = FlowBrowser()
    application = _to_review(db_session, browser, policy=SubmitPolicy(autonomy=AutonomyLevel.observe))
    assert browser.submits == []
    assert application.status is ApplicationStatus.waiting_for_user
    assert "submit_blocked" in _kinds(db_session, application)
    assert "Observe mode" in page_context(db_session, application, None)["waiting_reason"]


def test_supervised_auto_submits_only_when_every_answer_came_from_stored_facts(db_session) -> None:
    trusting = SubmitPolicy(autonomy=AutonomyLevel.supervised, trusted_after=0)

    human = FlowBrowser()
    application = _to_review(db_session, human, policy=trusting)
    assert human.submits == []
    reason = page_context(db_session, application, None)["waiting_reason"]
    assert "Auto-submit was not used" in reason

    stored_only = FlowBrowser(pages=("flow-info.html", "flow-review.html"))
    auto = _ready(db_session, "http://jobs.example/flow/auto")
    record_application_event(
        db_session,
        auto,
        event_type="resume_tailored",
        actor=EventActor.system,
        data={"reason": "Tailored.", "plan_error": None},
    )
    run_page(db_session, auto, stored_only, _facts(), policy=trusting)
    assert auto.status is ApplicationStatus.submitted
    assert stored_only.submits == [("submit", "#submit")]
    submitted = next(event for event in _events(db_session, auto) if event.event_type == "submitted")
    assert submitted.data["automatic"] is True


def test_supervised_respects_the_trust_threshold_and_the_daily_cap(db_session) -> None:
    browser = FlowBrowser(pages=("flow-info.html", "flow-review.html"))
    application = _ready(db_session, "http://jobs.example/flow/untrusted")
    record_application_event(
        db_session,
        application,
        event_type="resume_tailored",
        actor=EventActor.system,
        data={"reason": "Tailored.", "plan_error": None},
    )
    run_page(
        db_session,
        application,
        browser,
        _facts(),
        policy=SubmitPolicy(autonomy=AutonomyLevel.supervised, trusted_after=10),
    )
    assert browser.submits == []
    assert "10 are needed" in page_context(db_session, application, None)["waiting_reason"]

    capped = FlowBrowser(pages=("flow-info.html", "flow-review.html"))
    other = _ready(db_session, "http://jobs.example/flow/capped")
    run_page(
        db_session,
        other,
        capped,
        _facts(),
        policy=SubmitPolicy(autonomy=AutonomyLevel.supervised, trusted_after=0, daily_auto_submit_cap=0),
    )
    assert capped.submits == []
    assert "daily auto-submit limit" in page_context(db_session, other, None)["waiting_reason"]


def test_a_submit_without_confirmation_is_never_retried(db_session) -> None:
    browser = FlowBrowser(on_submit="silent")
    application = _to_review(db_session, browser)
    respond(db_session, application, browser, _facts(), action="confirm_submit")

    assert browser.submits == [("submit", "#submit")]
    assert application.status is ApplicationStatus.waiting_for_user
    context = page_context(db_session, application, None)
    assert context["pause_kind"] == "verify_submit"
    assert context["submit_attempted"] is True
    assert resume_ready(db_session, application) is False

    with pytest.raises(ApplicationTransitionError, match="already attempted"):
        respond(db_session, application, browser, _facts(), action="continue")
    with pytest.raises(ApplicationTransitionError):
        run_page(db_session, application, browser, _facts())
    assert len(browser.submits) == 1

    respond(db_session, application, None, _facts(), action="confirm_submitted")
    assert application.status is ApplicationStatus.submitted


def test_a_failed_submit_click_waits_for_the_user_to_check(db_session) -> None:
    browser = FlowBrowser(on_submit="error")
    application = _to_review(db_session, browser)
    respond(db_session, application, browser, _facts(), action="confirm_submit")
    assert len(browser.submits) == 1
    assert page_context(db_session, application, None)["pause_kind"] == "verify_submit"
    respond(db_session, application, None, _facts(), action="stop")
    assert application.status is ApplicationStatus.withdrawn


def test_an_interrupted_submit_is_recovered_as_needs_verification(db_session) -> None:
    browser = FlowBrowser()
    application = _ready(db_session, "http://jobs.example/flow/crash")
    run_page(db_session, application, browser, _facts())
    respond(db_session, application, browser, _facts(), action="edit", field_id="workAuthorization", value="Yes")
    respond(db_session, application, None, _facts(), action="confirm_submit")
    change_status(db_session, application, to=ApplicationStatus.applying, actor=EventActor.system, reason="Crash test.")
    application.submit_intent_at = utcnow()
    assert recover_interrupted_submit(db_session, application, EventActor.system) is True
    assert application.status is ApplicationStatus.waiting_for_user
    assert page_context(db_session, application, None)["pause_kind"] == "verify_submit"
    assert browser.submits == []


def test_a_duplicate_posting_that_was_already_applied_is_never_submitted(db_session) -> None:
    first = _to_review(db_session, FlowBrowser(), url="http://jobs.example/flow/same?src=a")
    respond(db_session, first, FlowBrowser(), _facts(), action="confirm_submit")
    assert first.status is ApplicationStatus.submitted

    browser = FlowBrowser()
    second = _ready(db_session, "http://jobs.example/flow/same?src=b")
    first.job.canonical_apply_url = second.job.canonical_apply_url = "http://jobs.example/flow/same"
    db_session.flush()
    run_page(db_session, second, browser, _facts())
    respond(db_session, second, browser, _facts(), action="edit", field_id="workAuthorization", value="Yes")
    assert browser.submits == []
    assert "already applied" in page_context(db_session, second, None)["waiting_reason"]


def test_visible_browser_walks_the_local_flow_and_submits_after_confirmation(settings, tmp_path) -> None:
    pytest.importorskip("playwright")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("visible Chromium needs a display")
    server = FormServer()
    server.start()
    manager = BrowserManager(profile_dir=tmp_path / "profile")
    app = create_app(settings)
    session = app.state.session_factory()
    try:
        manager.open()
        application = _ready(session, server.page("flow-info.html"))
        run_page(session, application, manager, _facts())
        assert page_context(session, application, None)["pause_kind"] == "questions"
        assert "Application Questions" in manager.form_html()

        respond(session, application, manager, _facts(), action="edit", field_id="workAuthorization", value="Yes")
        assert page_context(session, application, None)["pause_kind"] == "confirm_submit"
        assert "Check your answers" in manager.form_html()

        respond(session, application, manager, _facts(), action="confirm_submit")
        session.commit()
        assert application.status is ApplicationStatus.submitted
        assert "Thank you for applying" in manager.form_html()
        submits = [action for action in manager.actions if action.action == "submit"]
        assert len(submits) == 1
    finally:
        session.close()
        manager.close()
        server.stop()


def test_submit_authorizations_are_single_use() -> None:
    authorization = SubmitAuthorization("application 1", "confirmed")
    authorization.consume()
    with pytest.raises(SubmitRefused):
        authorization.consume()


def test_confirmation_text_needs_an_explicit_message() -> None:
    assert confirmation_text("<p>Thank you for applying to Northwind.</p>") is not None
    assert confirmation_text("<p>Your application was submitted.</p>") is not None
    assert confirmation_text("<p>Review your answers</p>") is None
    assert confirmation_text("<script>thank you for applying</script>") is None
    kept = confirmation_text("<html><head><title>Review</title></head><body><h2>Thank you for applying</h2></body></html>")
    assert kept == "Thank you for applying"


def test_answers_from_stored_facts_count_for_supervised(db_session) -> None:
    applicant = ApplicantData(
        facts=[
            *_facts().facts,
            KnownFact(key="contact.first_name", value="Ada", confidence=1, source=DecisionSource.answer_bank),
        ]
    )
    browser = FlowBrowser(pages=("flow-info.html", "flow-review.html"))
    application = _ready(db_session, "http://jobs.example/flow/bank")
    record_application_event(
        db_session,
        application,
        event_type="resume_tailored",
        actor=EventActor.system,
        data={"reason": "Tailored.", "plan_error": None},
    )
    run_page(
        db_session,
        application,
        browser,
        applicant,
        policy=SubmitPolicy(autonomy=AutonomyLevel.supervised, trusted_after=0),
    )
    assert application.status is ApplicationStatus.submitted
