"""Walk one application page by page, fill only known fields, and stop for the user.

Submitting happens only through the submit gate, at most once per application.
"""

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.apply.decisions import (
    ApplicantData,
    DecisionSource,
    FieldAction,
    FieldDecision,
    KnownFact,
    decide,
    is_consequential,
)
from jobhunter.apply.fields import ApplicationField, NavigationButton, WorkdayPage
from jobhunter.apply.submission import (
    SubmitDecision,
    SubmitPolicy,
    already_applied,
    answers_fingerprint,
    authorize,
    confirmation_text,
)
from jobhunter.apply.workday import WorkdayAdapter, WorkdayPageError
from jobhunter.applications import change_status
from jobhunter.browser.errors import BrowserError
from jobhunter.browser.manager import BrowserManager, SubmitAuthorization
from jobhunter.db.models import Application, ApplicationAnswer, ApplicationEvent
from jobhunter.db.records import record_application_event
from jobhunter.domain.enums import ApplicationStatus, EventActor, ValueSource
from jobhunter.domain.errors import ApplicationTransitionError
from jobhunter.domain.time import utcnow

MAX_PAGES = 12
CONFIRMATION_CHECKS = 5
_REASON_LIMIT = 500
_VALUE_SOURCES = {
    DecisionSource.profile: ValueSource.profile,
    DecisionSource.answer_bank: ValueSource.answer_bank,
    DecisionSource.application: ValueSource.human,
    DecisionSource.resume: ValueSource.profile,
    DecisionSource.job: ValueSource.profile,
    DecisionSource.none: ValueSource.human,
}
_PAUSE_EVENTS = ("waiting_for_user", "resume_requested", "submit_confirmed")
_REVIEW_ACTIONS = frozenset({"approve", "edit", "skip", "stop", "continue", "confirm_submit", "confirm_submitted"})


def run_page(
    session: Session,
    application: Application,
    browser: BrowserManager,
    applicant: ApplicantData,
    *,
    actor: EventActor = EventActor.system,
    screenshot_dir: Path | None = None,
    policy: SubmitPolicy | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Application:
    """Open the job URL and walk its pages. Unknown answers and unexpected pages wait for the user."""

    _start(session, application, actor)
    _walk(
        session,
        application,
        browser,
        applicant,
        actor,
        screenshot_dir=screenshot_dir,
        policy=policy or SubmitPolicy(),
        cancel=cancel or (lambda: False),
    )
    return application


def resume_page(
    session: Session,
    application: Application,
    browser: BrowserManager,
    applicant: ApplicantData,
    answers: dict[str, str],
    *,
    actor: EventActor = EventActor.user,
    sources: dict[str, ValueSource] | None = None,
    screenshot_dir: Path | None = None,
    policy: SubmitPolicy | None = None,
) -> Application:
    """Store the answers the user just provided, then continue the application from the start."""

    if application.status is not ApplicationStatus.waiting_for_user:
        raise ApplicationTransitionError("resume is only for an application that is waiting for the user")
    cleaned = {field_id: value.strip() for field_id, value in answers.items()}
    if not cleaned or any(not value for value in cleaned.values()):
        raise ApplicationTransitionError("every resumed answer needs a value")
    for field_id, value in cleaned.items():
        record_answer(session, application, field_id, value, (sources or {}).get(field_id, ValueSource.human), actor)
    request_resume(session, application, actor, f"Answers provided for {', '.join(sorted(cleaned))}.")
    return run_page(
        session,
        application,
        browser,
        applicant,
        actor=actor,
        screenshot_dir=screenshot_dir,
        policy=policy,
    )


def respond(
    session: Session,
    application: Application,
    browser: BrowserManager | None,
    applicant: ApplicantData,
    *,
    action: str,
    field_id: str = "",
    value: str | None = None,
    actor: EventActor = EventActor.user,
    screenshot_dir: Path | None = None,
    policy: SubmitPolicy | None = None,
) -> Application:
    """Record one review decision. With a browser it continues at once; without one the agent continues later."""

    if action not in _REVIEW_ACTIONS:
        raise ApplicationTransitionError("unknown review action")
    if application.status is not ApplicationStatus.waiting_for_user:
        raise ApplicationTransitionError("review is only for an application that is waiting for the user")
    if action == "stop":
        change_status(
            session,
            application,
            to=ApplicationStatus.withdrawn,
            actor=actor,
            reason="Stopped by the user during review.",
        )
        return application
    if action == "confirm_submitted":
        if application.submit_intent_at is None:
            raise ApplicationTransitionError("only an attempted submit can be confirmed as sent")
        change_status(
            session,
            application,
            to=ApplicationStatus.submitted,
            actor=actor,
            reason="You confirmed the application went through.",
        )
        return application
    if application.submit_intent_at is not None:
        raise ApplicationTransitionError(
            "a submit was already attempted; confirm whether it went through or stop the application"
        )
    if action == "confirm_submit":
        ready = _latest_event(session, application, "submit_ready")
        if pause_kind(session, application) != "confirm_submit" or ready is None:
            raise ApplicationTransitionError("this application is not waiting for a submit confirmation")
        _record(
            session,
            application,
            "submit_confirmed",
            actor,
            "You confirmed the submission.",
            fingerprint=ready.data.get("fingerprint"),
        )
    elif action == "continue":
        if pending_fields(session, application):
            raise ApplicationTransitionError("answer or skip the open questions first")
        if pause_kind(session, application) == "confirm_submit":
            raise ApplicationTransitionError("confirm the submission or stop the application")
        request_resume(session, application, actor, "You asked to continue.")
    else:
        pending = _pending_match(session, application, field_id)
        label = pending.get("label") or field_id
        if action == "skip":
            _record(
                session,
                application,
                "answer_skipped",
                actor,
                f"Skipped {label}.",
                field_id=field_id,
                label=label,
                origin="user",
            )
        elif action == "approve":
            proposed = pending.get("proposed_value")
            if not isinstance(proposed, str) or not proposed.strip():
                raise ApplicationTransitionError("there is no suggested answer to approve")
            record_answer(session, application, field_id, proposed.strip(), ValueSource.llm_draft_approved, actor)
        else:
            cleaned = (value or "").strip()
            if not cleaned:
                raise ApplicationTransitionError("an edited answer needs a value")
            record_answer(session, application, field_id, cleaned, ValueSource.human, actor)
        if not pending_fields(session, application):
            request_resume(session, application, actor, "Every open question was answered or skipped.")
    if browser is not None and resume_ready(session, application):
        run_page(
            session,
            application,
            browser,
            applicant,
            actor=actor,
            screenshot_dir=screenshot_dir,
            policy=policy,
        )
    return application


def record_answer(
    session: Session,
    application: Application,
    field_id: str,
    value: str,
    source: ValueSource,
    actor: EventActor,
) -> ApplicationAnswer:
    """Store one answer for a field on the latest inspected page, checked against its choices."""

    snapshot = next(
        (field for field in _latest_inspection(session, application) or [] if field.get("field_id") == field_id),
        None,
    )
    if snapshot is None:
        raise ApplicationTransitionError(f"no inspected field matches {field_id}")
    cleaned = value.strip()
    if not cleaned:
        raise ApplicationTransitionError("an answer needs a value")
    options = [str(option) for option in snapshot.get("options") or []]
    if options and snapshot.get("type") in {"dropdown", "radio"}:
        match = next((option for option in options if _normalize(option) == _normalize(cleaned)), None)
        if match is None:
            raise ApplicationTransitionError(f"{cleaned!r} is not one of the choices: {', '.join(options)}")
        cleaned = match
    answer = _upsert_answer(
        session,
        application,
        field_key=field_id,
        label=str(snapshot.get("label") or field_id),
        field_type=str(snapshot.get("type") or "text"),
        required=bool(snapshot.get("required")),
        canonical_key=snapshot.get("canonical_key"),
        value=cleaned,
        source=source,
    )
    origin = _origin(source)
    label = snapshot.get("label") or field_id
    _record(
        session,
        application,
        "answer_recorded",
        actor,
        f"Approved the suggested answer for {label}." if origin == "suggested" else f"Saved your answer for {label}.",
        field_id=field_id,
        value=cleaned,
        source=source.value,
        origin=origin,
    )
    return answer


def request_resume(session: Session, application: Application, actor: EventActor, reason: str) -> None:
    _record(session, application, "resume_requested", actor, reason)


def resume_ready(session: Session, application: Application) -> bool:
    """Waiting, and the user has done what the latest pause asked for."""

    if application.status is not ApplicationStatus.waiting_for_user or application.submit_intent_at is not None:
        return False
    event = session.scalar(
        select(ApplicationEvent)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.event_type.in_(_PAUSE_EVENTS),
        )
        .order_by(ApplicationEvent.id.desc())
    )
    return event is not None and event.event_type != "waiting_for_user"


def pause_kind(session: Session, application: Application) -> str | None:
    event = _latest_event(session, application, "waiting_for_user")
    if event is None:
        return None
    return str(event.data.get("kind") or "needs_look")


def unresolved_required(session: Session, application: Application) -> list[str]:
    inspected = _latest_inspection(session, application)
    if inspected is None:
        return ["page"]
    answered = _answered_ids(session, application)
    missing: list[str] = []
    for field in inspected:
        field_id = str(field.get("field_id") or "")
        if not field_id or field_id in answered:
            continue
        action = field.get("action")
        if action == FieldAction.require_user.value or field.get("required"):
            missing.append(field_id)
    return missing


def pending_fields(session: Session, application: Application) -> list[dict]:
    """Questions still waiting on the user, from the latest inspection."""

    if application.status is not ApplicationStatus.waiting_for_user or pause_kind(session, application) != "questions":
        return []
    answered = _answered_ids(session, application)
    skipped = _skipped_ids(session, application)
    pending: list[dict] = []
    for field in _latest_inspection(session, application) or []:
        action = field.get("action")
        field_id = field.get("field_id")
        if field_id in answered or field_id in skipped:
            continue
        needs_user = action == FieldAction.require_user.value or (
            action == FieldAction.suggest_and_ask.value and field.get("required")
        )
        if needs_user:
            pending.append(field)
    return pending


def waiting_applications(session: Session) -> list[Application]:
    return list(
        session.scalars(
            select(Application)
            .where(Application.status == ApplicationStatus.waiting_for_user)
            .order_by(Application.status_changed_at, Application.id)
        ).all()
    )


def page_context(session: Session, application: Application, screenshot_dir: Path | None) -> dict:
    """The latest pause, including whether a screenshot file is on disk."""

    empty = {
        "page_url": None,
        "page_title": None,
        "waiting_reason": None,
        "has_screenshot": False,
        "pause_kind": None,
        "resume_queued": False,
        "submit_attempted": application.submit_intent_at is not None,
    }
    if application.status is not ApplicationStatus.waiting_for_user:
        return empty
    event = _latest_event(session, application, "waiting_for_user")
    data = event.data if event is not None else {}
    name = data.get("screenshot")
    has_screenshot = False
    if isinstance(name, str) and screenshot_dir is not None and name == screenshot_name(application.id):
        has_screenshot = screenshot_path(screenshot_dir, application.id).is_file()
    title = data.get("heading") or data.get("title") or None
    return {
        **empty,
        "page_url": data.get("url") or None,
        "page_title": title if isinstance(title, str) and title.strip() else None,
        "waiting_reason": data.get("reason") or None,
        "has_screenshot": has_screenshot,
        "pause_kind": pause_kind(session, application),
        "resume_queued": resume_ready(session, application),
    }


def recover_interrupted_submit(session: Session, application: Application, actor: EventActor) -> bool:
    """A submit that started but was never confirmed is never retried. The user checks it."""

    if application.submit_intent_at is None or application.submitted_at is not None:
        return False
    if application.status is not ApplicationStatus.applying:
        return False
    _pause(
        session,
        application,
        actor,
        "A submit was started but not confirmed before the agent stopped. It will not be tried again. "
        "Check the site or your email, then confirm whether it went through.",
        kind="verify_submit",
    )
    return True


def pause_application(session: Session, application: Application, actor: EventActor, reason: str) -> None:
    """Hand an applying application to the user without touching the browser."""

    if application.status in (ApplicationStatus.applying, ApplicationStatus.waiting_for_user):
        _pause(session, application, actor, reason)


def screenshot_name(application_id: int) -> str:
    return f"application-{application_id}.png"


def screenshot_path(directory: Path, application_id: int) -> Path:
    return directory / screenshot_name(application_id)


def _start(session: Session, application: Application, actor: EventActor) -> None:
    status = application.status
    if status is ApplicationStatus.waiting_for_user:
        if not resume_ready(session, application):
            raise ApplicationTransitionError("this application is waiting for the user; answer the open questions first")
        change_status(session, application, to=ApplicationStatus.applying, actor=actor, reason="Continuing the application.")
        _record(session, application, "resumed", actor, "Continuing the application from the first page.")
        return
    if status is ApplicationStatus.applying:
        if application.submit_intent_at is not None:
            raise ApplicationTransitionError("a submit was already attempted for this application")
        _record(session, application, "resumed", actor, "Reopening the application from the first page.")
        return
    if status is not ApplicationStatus.ready_to_apply:
        raise ApplicationTransitionError(f"applying starts from ready to apply, not {status.value}")
    change_status(session, application, to=ApplicationStatus.applying, actor=actor, reason="Opened the application page.")
    application.attempts += 1


def _walk(
    session: Session,
    application: Application,
    browser: BrowserManager,
    applicant: ApplicantData,
    actor: EventActor,
    *,
    screenshot_dir: Path | None,
    policy: SubmitPolicy,
    cancel: Callable[[], bool],
) -> None:
    page = _open_page(session, application, browser, actor, screenshot_dir=screenshot_dir)
    if page is None:
        return
    adapter = WorkdayAdapter(browser)
    for index in range(1, MAX_PAGES + 1):
        application.current_page = index
        if not _classify(session, application, browser, applicant, actor, page, screenshot_dir=screenshot_dir):
            return
        if cancel():
            _record(session, application, "interrupted", actor, "Stopped between pages. It will continue from the start.")
            return
        forward = _button(page, "next")
        if forward is None:
            if _button(page, "submit") is not None:
                _at_submit(session, application, browser, actor, page, screenshot_dir=screenshot_dir, policy=policy)
            else:
                _pause(
                    session,
                    application,
                    actor,
                    "This page has no Next or Submit button, so there is no safe way forward.",
                    browser=browser,
                    page=page,
                    screenshot_dir=screenshot_dir,
                )
            return
        before = _signature(page)
        try:
            adapter.press(forward.button_id)
            _settle(browser)
            page = adapter.read_page()
        except (BrowserError, WorkdayPageError) as exc:
            _pause(
                session,
                application,
                actor,
                f"Stopped without guessing. {exc}",
                browser=browser,
                screenshot_dir=screenshot_dir,
            )
            return
        if _signature(page) == before:
            _pause(
                session,
                application,
                actor,
                f"The page did not change after {forward.label or 'Next'}. It may be showing an error.",
                browser=browser,
                page=page,
                screenshot_dir=screenshot_dir,
            )
            return
        _record(
            session,
            application,
            "page_advanced",
            actor,
            f"Moved to page {index + 1}: {page.heading or page.title or 'untitled'}.",
            url=page.url,
        )
    _pause(
        session,
        application,
        actor,
        f"Stopped after {MAX_PAGES} pages without reaching the submit step.",
        browser=browser,
        page=page,
        screenshot_dir=screenshot_dir,
    )


def _at_submit(
    session: Session,
    application: Application,
    browser: BrowserManager,
    actor: EventActor,
    page: WorkdayPage,
    *,
    screenshot_dir: Path | None,
    policy: SubmitPolicy,
) -> None:
    fingerprint = answers_fingerprint(session, application)
    decision = authorize(
        session,
        application,
        unresolved=unresolved_required(session, application),
        fingerprint=fingerprint,
        policy=policy,
    )
    if decision.allowed:
        _submit(session, application, browser, actor, decision, screenshot_dir=screenshot_dir)
        return
    if decision.needs_confirmation:
        _record(session, application, "submit_ready", actor, decision.reason, fingerprint=fingerprint)
        _pause(
            session,
            application,
            actor,
            decision.reason,
            browser=browser,
            page=page,
            screenshot_dir=screenshot_dir,
            kind="confirm_submit",
        )
        return
    _record(session, application, "submit_blocked", actor, decision.reason)
    _pause(session, application, actor, decision.reason, browser=browser, page=page, screenshot_dir=screenshot_dir)


def _submit(
    session: Session,
    application: Application,
    browser: BrowserManager,
    actor: EventActor,
    decision: SubmitDecision,
    *,
    screenshot_dir: Path | None,
) -> None:
    application.submit_intent_at = utcnow()
    _record(session, application, "submit_intent", actor, decision.reason, automatic=decision.automatic)
    session.commit()
    authorization = SubmitAuthorization(f"application {application.id}", decision.reason)
    try:
        WorkdayAdapter(browser).submit(authorization)
    except (BrowserError, WorkdayPageError) as exc:
        _pause(
            session,
            application,
            actor,
            f"The submit may not have gone through ({exc}). It will not be tried again. "
            "Check the site or your email, then confirm whether it went through.",
            browser=browser,
            screenshot_dir=screenshot_dir,
            kind="verify_submit",
        )
        return
    confirmation = None
    for _ in range(CONFIRMATION_CHECKS):
        _settle(browser)
        try:
            html = browser.form_html()
        except BrowserError:
            continue
        confirmation = confirmation_text(html)
        if confirmation is not None:
            break
    if confirmation is None:
        _pause(
            session,
            application,
            actor,
            "Submit was pressed, but no confirmation appeared. It will not be tried again. "
            "Check the site or your email, then confirm whether it went through.",
            browser=browser,
            screenshot_dir=screenshot_dir,
            kind="verify_submit",
        )
        return
    application.confirmation_text = confirmation[:2000]
    screenshot = _capture_screenshot(browser, application, screenshot_dir)
    change_status(
        session,
        application,
        to=ApplicationStatus.submitted,
        actor=actor,
        reason=_clip(f"Submitted. The site said: {confirmation}"),
    )
    _record(
        session,
        application,
        "submitted",
        actor,
        _clip(f"Submitted. The site said: {confirmation}"),
        automatic=decision.automatic,
        screenshot=screenshot,
    )


def _classify(
    session: Session,
    application: Application,
    browser: BrowserManager,
    applicant: ApplicantData,
    actor: EventActor,
    page: WorkdayPage,
    *,
    screenshot_dir: Path | None = None,
) -> bool:
    """Fill what is known on this page. Returns True when nothing on it needs the user."""

    context = _context(session, application, applicant)
    stored = _stored_answers(session, application)
    skipped = _skipped_ids(session, application)
    classified: list[tuple[ApplicationField, FieldDecision]] = []
    for field in page.fields:
        decision = decide(field, context)
        classified.append((field, decision))
        _record(
            session,
            application,
            "field_classified",
            actor,
            f"{field.label or field.field_id} classified as {decision.action.value.replace('_', ' ')}.",
            field_id=field.field_id,
            label=field.label,
            action=decision.action.value,
            proposed_value=decision.proposed_value,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            source=decision.source.value,
            canonical_key=decision.canonical_key,
            required=field.required,
        )
    _record(
        session,
        application,
        "page_inspected",
        actor,
        f"Inspected {len(classified)} fields.",
        url=page.url,
        page=application.current_page,
        fields=[_snapshot(field, decision) for field, decision in classified],
    )
    adapter = WorkdayAdapter(browser)
    blockers: list[str] = []
    for field, decision in classified:
        label = field.label or field.field_id
        stored_answer = stored.get(field.field_id)
        if stored_answer is not None:
            stored_value = stored_answer.value
            if not _page_has_value(field, stored_value):
                if not _write(adapter, field, stored_value):
                    _pause(
                        session,
                        application,
                        actor,
                        f"Could not enter the saved answer for {label}.",
                        browser=browser,
                        page=page,
                        screenshot_dir=screenshot_dir,
                    )
                    return False
                _record(
                    session,
                    application,
                    "field_filled",
                    actor,
                    f"Entered the saved answer for {label}.",
                    field_id=field.field_id,
                    value=stored_value,
                    source=stored_answer.value_source.value,
                    origin=_origin(stored_answer.value_source),
                )
            continue
        if field.field_id in skipped:
            continue
        if decision.action is FieldAction.auto_fill and decision.proposed_value is not None:
            source = _VALUE_SOURCES[decision.source]
            if not _page_has_value(field, decision.proposed_value):
                if not _write(adapter, field, decision.proposed_value):
                    _pause(
                        session,
                        application,
                        actor,
                        f"Could not fill {label}.",
                        browser=browser,
                        page=page,
                        screenshot_dir=screenshot_dir,
                    )
                    return False
                reason = f"Filled {label} from {decision.source.value.replace('_', ' ')}."
            else:
                reason = f"{label} already had the stored value."
            _save_answer(session, application, field, decision, decision.proposed_value, source)
            _record(
                session,
                application,
                "field_filled",
                actor,
                reason,
                field_id=field.field_id,
                value=decision.proposed_value,
                source=decision.source.value,
            )
            continue
        if decision.action is FieldAction.suggest_and_ask:
            _record(
                session,
                application,
                "suggestion_created",
                actor,
                decision.reasoning,
                field_id=field.field_id,
                label=field.label,
                proposed_value=decision.proposed_value,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                source=decision.source.value,
                required=field.required,
            )
            if field.required:
                blockers.append(label)
            continue
        if decision.action is FieldAction.require_user:
            _record(
                session,
                application,
                "input_required",
                actor,
                decision.reasoning,
                field_id=field.field_id,
                label=field.label,
                proposed_value=decision.proposed_value,
                reasoning=decision.reasoning,
                required=field.required,
            )
            blockers.append(label)
            continue
        _record(
            session,
            application,
            "field_skipped",
            actor,
            decision.reasoning,
            field_id=field.field_id,
            label=field.label,
            required=field.required,
        )
        if field.required:
            blockers.append(label)
    if blockers:
        _pause(
            session,
            application,
            actor,
            "Waiting for answers to " + ", ".join(blockers) + ".",
            browser=browser,
            page=page,
            screenshot_dir=screenshot_dir,
            kind="questions",
        )
        return False
    _record(session, application, "page_ready", actor, "Every field on this page is resolved.")
    return True


def _open_page(
    session: Session,
    application: Application,
    browser: BrowserManager,
    actor: EventActor,
    *,
    screenshot_dir: Path | None = None,
) -> WorkdayPage | None:
    url = (application.job.apply_url or "").strip()
    if not url:
        _pause(session, application, actor, "This job has no application URL.", screenshot_dir=screenshot_dir)
        return None
    try:
        browser.navigate(url)
        html = browser.form_html()
        if already_applied(html):
            _pause(
                session,
                application,
                actor,
                "The site says you already applied to this job. Nothing was entered.",
                browser=browser,
                screenshot_dir=screenshot_dir,
            )
            return None
        page = WorkdayAdapter(browser).read_page()
    except (BrowserError, WorkdayPageError) as exc:
        _pause(
            session,
            application,
            actor,
            f"Stopped without guessing. {exc}",
            browser=browser,
            screenshot_dir=screenshot_dir,
        )
        return None
    application.current_page = 1
    _record(session, application, "page_opened", actor, f"Opened {url}.", url=url, title=page.title)
    return page


def _write(adapter: WorkdayAdapter, field: ApplicationField, value: str) -> bool:
    try:
        if field.type in {"text", "free_text"}:
            adapter.fill(field.field_id, value)
        elif field.type in {"dropdown", "radio"}:
            adapter.choose(field.field_id, value)
        elif field.type == "checkbox":
            adapter.set_checked(field.field_id, _normalize(value) in {"yes", "true"})
        elif field.type == "file":
            adapter.upload(field.field_id, Path(value))
        else:
            return False
    except (BrowserError, WorkdayPageError, OSError):
        return False
    return True


def _pause(
    session: Session,
    application: Application,
    actor: EventActor,
    reason: str,
    *,
    browser: BrowserManager | None = None,
    page: WorkdayPage | None = None,
    screenshot_dir: Path | None = None,
    kind: str = "needs_look",
) -> None:
    cleaned = _clip(reason)
    context: dict[str, object] = {"kind": kind}
    if page is not None:
        if page.url:
            context["url"] = page.url
        if page.title:
            context["title"] = page.title
        if page.heading:
            context["heading"] = page.heading
    current_url = _browser_url(browser)
    if current_url and "url" not in context:
        context["url"] = current_url
    screenshot = _capture_screenshot(browser, application, screenshot_dir)
    if screenshot:
        context["screenshot"] = screenshot
    _record(session, application, "waiting_for_user", actor, cleaned, **context)
    if application.status is ApplicationStatus.waiting_for_user:
        return
    change_status(session, application, to=ApplicationStatus.waiting_for_user, actor=actor, reason=cleaned)


def _context(session: Session, application: Application, applicant: ApplicantData) -> ApplicantData:
    job = application.job
    facts = [
        *applicant.facts,
        *[
            KnownFact(key=answer.canonical_key, value=answer.value, confidence=1, source=DecisionSource.application)
            for answer in _answers(session, application)
            if answer.canonical_key and answer.value
        ],
    ]
    return applicant.model_copy(
        update={
            "facts": facts,
            "job_title": applicant.job_title or job.title,
            "company": applicant.company or job.company_name,
            "job_description": applicant.job_description or job.description_text,
        }
    )


def _save_answer(
    session: Session,
    application: Application,
    field: ApplicationField,
    decision: FieldDecision,
    value: str,
    source: ValueSource,
) -> None:
    _upsert_answer(
        session,
        application,
        field_key=field.field_id,
        label=field.label or field.field_id,
        field_type=field.type,
        required=field.required,
        canonical_key=decision.canonical_key,
        value=value,
        source=source,
    )


def _upsert_answer(
    session: Session,
    application: Application,
    *,
    field_key: str,
    label: str,
    field_type: str,
    required: bool,
    canonical_key: str | None,
    value: str,
    source: ValueSource,
) -> ApplicationAnswer:
    existing = session.scalar(
        select(ApplicationAnswer).where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.field_key == field_key,
        )
    )
    if existing is None:
        existing = ApplicationAnswer(
            application_id=application.id,
            field_key=field_key[:200],
            label=label[:300],
            field_type=field_type[:32],
            required=required,
            canonical_key=canonical_key,
            value=value,
            value_source=source,
            consequential=is_consequential(canonical_key),
            page_index=application.current_page,
        )
        session.add(existing)
    else:
        existing.value = value
        existing.value_source = source
        existing.label = label[:300]
        existing.required = required
        existing.canonical_key = canonical_key
        existing.consequential = is_consequential(canonical_key)
    session.flush()
    return existing


def _snapshot(field: ApplicationField, decision: FieldDecision) -> dict:
    return {
        "field_id": field.field_id,
        "label": field.label,
        "type": field.type,
        "required": field.required,
        "action": decision.action.value,
        "proposed_value": decision.proposed_value,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        "source": decision.source.value,
        "canonical_key": decision.canonical_key,
        "options": list(field.options),
    }


def _record(
    session: Session,
    application: Application,
    event_type: str,
    actor: EventActor,
    reason: str,
    **data: object,
) -> None:
    payload = {"reason": _clip(reason), **data}
    record_application_event(session, application, event_type=event_type, actor=actor, data=payload)


def _latest_event(session: Session, application: Application, event_type: str) -> ApplicationEvent | None:
    return session.scalar(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application.id, ApplicationEvent.event_type == event_type)
        .order_by(ApplicationEvent.id.desc())
    )


def _latest_inspection(session: Session, application: Application) -> list[dict] | None:
    event = _latest_event(session, application, "page_inspected")
    if event is None:
        return None
    fields = event.data.get("fields")
    if not isinstance(fields, list):
        return None
    return [field for field in fields if isinstance(field, dict)]


def _answers(session: Session, application: Application) -> list[ApplicationAnswer]:
    return list(
        session.scalars(
            select(ApplicationAnswer)
            .where(ApplicationAnswer.application_id == application.id)
            .order_by(ApplicationAnswer.id)
        ).all()
    )


def _answered_ids(session: Session, application: Application) -> set[str]:
    return {answer.field_key for answer in _answers(session, application) if answer.value and answer.value.strip()}


def _stored_answers(session: Session, application: Application) -> dict[str, ApplicationAnswer]:
    return {
        answer.field_key: answer
        for answer in _answers(session, application)
        if answer.value and answer.value.strip()
    }


def _origin(source: ValueSource) -> str:
    if source is ValueSource.llm_draft_approved:
        return "suggested"
    if source is ValueSource.human:
        return "user"
    return "stored"


def _skipped_ids(session: Session, application: Application) -> set[str]:
    events = session.scalars(
        select(ApplicationEvent).where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.event_type == "answer_skipped",
        )
    )
    return {str(event.data.get("field_id")) for event in events if event.data.get("field_id")}


def _pending_match(session: Session, application: Application, field_id: str) -> dict:
    if not field_id:
        raise ApplicationTransitionError("a review answer needs the question it applies to")
    for field in pending_fields(session, application):
        if field.get("field_id") == field_id:
            return field
    raise ApplicationTransitionError("that question is not waiting for an answer")


def _button(page: WorkdayPage, kind: str) -> NavigationButton | None:
    return next((button for button in page.navigation if button.kind == kind), None)


def _signature(page: WorkdayPage) -> tuple:
    return (page.url, page.heading, page.title, tuple(field.field_id for field in page.fields))


def _settle(browser: BrowserManager) -> None:
    settle = getattr(browser, "settle", None)
    if settle is None:
        return
    try:
        settle()
    except BrowserError:
        pass


def _capture_screenshot(
    browser: BrowserManager | None,
    application: Application,
    screenshot_dir: Path | None,
) -> str | None:
    if browser is None or screenshot_dir is None:
        return None
    capture = getattr(browser, "screenshot", None)
    if capture is None:
        return None
    destination = screenshot_path(screenshot_dir, application.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        capture(destination)
    except Exception:
        if destination.exists():
            destination.unlink()
        return None
    if not destination.is_file():
        return None
    return destination.name


def _browser_url(browser: BrowserManager | None) -> str | None:
    if browser is None:
        return None
    try:
        value = browser.location
    except Exception:
        return None
    if isinstance(value, str) and value.strip() and value != "about:blank":
        return value
    return None


def _page_has_value(field: ApplicationField, value: str) -> bool:
    current = field.current_value.strip()
    if not current:
        return False
    if field.type == "checkbox":
        return (_normalize(current) in {"yes", "true"}) == (_normalize(value) in {"yes", "true"})
    return _normalize(current) == _normalize(value)


def _normalize(text: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in text)
    return " ".join(cleaned.split())


def _clip(text: str) -> str:
    cleaned = " ".join(text.split()) or "Stopped without guessing."
    if len(cleaned) <= _REASON_LIMIT:
        return cleaned
    return cleaned[: _REASON_LIMIT - 1].rstrip() + "…"
