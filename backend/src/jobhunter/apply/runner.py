"""Open one application page, fill only known fields, and stop for the user. It never submits."""

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
from jobhunter.apply.fields import ApplicationField, WorkdayPage
from jobhunter.apply.workday import WorkdayAdapter, WorkdayPageError
from jobhunter.applications import change_status
from jobhunter.browser.errors import BrowserError, SubmitRefused
from jobhunter.browser.manager import BrowserManager
from jobhunter.db.models import Application, ApplicationAnswer, ApplicationEvent
from jobhunter.db.records import record_application_event
from jobhunter.domain.enums import ApplicationStatus, EventActor, ValueSource
from jobhunter.domain.errors import ApplicationTransitionError

_REASON_LIMIT = 500
_VALUE_SOURCES = {
    DecisionSource.profile: ValueSource.profile,
    DecisionSource.answer_bank: ValueSource.answer_bank,
    DecisionSource.application: ValueSource.human,
    DecisionSource.resume: ValueSource.profile,
    DecisionSource.job: ValueSource.profile,
    DecisionSource.none: ValueSource.human,
}


def run_page(
    session: Session,
    application: Application,
    browser: BrowserManager,
    applicant: ApplicantData,
    *,
    actor: EventActor = EventActor.system,
) -> Application:
    """Open the job URL and act on the fields. Unexpected pages wait for the user."""

    if application.status is ApplicationStatus.waiting_for_user:
        raise ApplicationTransitionError("this application is waiting for answers; resume it instead")
    _enter_applying(session, application, actor, "Opened the application page.")
    page = _open_page(session, application, browser, actor)
    if page is not None:
        _classify(session, application, browser, applicant, actor, page, resolved=set())
    return application


def resume_page(
    session: Session,
    application: Application,
    browser: BrowserManager,
    applicant: ApplicantData,
    answers: dict[str, str],
    *,
    actor: EventActor = EventActor.user,
) -> Application:
    """Enter the answers the user just provided, then continue the same page."""

    if application.status is not ApplicationStatus.waiting_for_user:
        raise ApplicationTransitionError("resume is only for an application that is waiting for the user")
    cleaned = {field_id: value.strip() for field_id, value in answers.items()}
    if not cleaned or any(not value for value in cleaned.values()):
        raise ApplicationTransitionError("every resumed answer needs a value")
    change_status(
        session,
        application,
        to=ApplicationStatus.applying,
        actor=actor,
        reason=_clip(f"Resumed with answers for {', '.join(sorted(cleaned))}."),
    )
    _record(session, application, "resumed", actor, f"Resumed with answers for {', '.join(sorted(cleaned))}.")
    page = _open_page(session, application, browser, actor)
    if page is None:
        return application
    unknown = [field_id for field_id in cleaned if not _has_field(page, field_id)]
    if unknown:
        _pause(session, application, actor, f"No field on this page matches {', '.join(unknown)}.")
        return application
    adapter = WorkdayAdapter(browser)
    for field_id, value in cleaned.items():
        field = page.field(field_id)
        if not _write(adapter, field, value):
            _pause(session, application, actor, f"Could not enter the answer for {field.label or field_id}.")
            return application
        decision = decide(field, _context(session, application, applicant))
        _save_answer(session, application, field, decision, value, ValueSource.human)
        _record(
            session,
            application,
            "field_filled",
            actor,
            f"Entered the answer for {field.label or field_id}.",
            field_id=field.field_id,
            value=value,
            source=ValueSource.human.value,
        )
    try:
        page = WorkdayAdapter(browser).read_page()
    except (BrowserError, WorkdayPageError) as exc:
        _pause(session, application, actor, f"Stopped without guessing. {exc}")
        return application
    _classify(session, application, browser, applicant, actor, page, resolved=set(cleaned))
    return application


def refuse_submit(session: Session, application: Application, *, actor: EventActor = EventActor.user) -> None:
    """Record that submission was refused. This does not touch the browser."""

    missing = unresolved_required(session, application)
    if missing:
        reason = f"Refusing to submit. Required fields are not resolved: {', '.join(missing)}."
    else:
        reason = "Refusing to submit. Autonomous submission is not enabled."
    _record(session, application, "submit_blocked", actor, reason, unresolved=missing)
    raise SubmitRefused(reason)


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

    if application.status is not ApplicationStatus.waiting_for_user:
        return []
    answered = _answered_ids(session, application)
    pending: list[dict] = []
    for field in _latest_inspection(session, application) or []:
        action = field.get("action")
        field_id = field.get("field_id")
        if field_id in answered:
            continue
        needs_user = action == FieldAction.require_user.value or (
            action == FieldAction.suggest_and_ask.value and field.get("required")
        )
        if needs_user:
            pending.append(field)
    return pending


def _classify(
    session: Session,
    application: Application,
    browser: BrowserManager,
    applicant: ApplicantData,
    actor: EventActor,
    page: WorkdayPage,
    *,
    resolved: set[str],
) -> None:
    context = _context(session, application, applicant)
    stored = _stored_values(session, application)
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
        fields=[_snapshot(field, decision) for field, decision in classified],
    )
    adapter = WorkdayAdapter(browser)
    blockers: list[str] = []
    for field, decision in classified:
        if field.field_id in resolved:
            continue
        stored_value = stored.get(field.field_id)
        if stored_value is not None:
            if not _page_has_value(field, stored_value):
                if not _write(adapter, field, stored_value):
                    _pause(session, application, actor, f"Could not restore the saved answer for {field.label or field.field_id}.")
                    return
                _record(
                    session,
                    application,
                    "field_filled",
                    actor,
                    f"Restored the saved answer for {field.label or field.field_id}.",
                    field_id=field.field_id,
                    value=stored_value,
                    source=ValueSource.human.value,
                )
            continue
        if decision.action is FieldAction.auto_fill and decision.proposed_value is not None:
            if _page_has_value(field, decision.proposed_value):
                _save_answer(session, application, field, decision, decision.proposed_value, _VALUE_SOURCES[decision.source])
                _record(
                    session,
                    application,
                    "field_filled",
                    actor,
                    f"{field.label or field.field_id} already had the stored value.",
                    field_id=field.field_id,
                    value=decision.proposed_value,
                    source=decision.source.value,
                )
                continue
            if not _write(adapter, field, decision.proposed_value):
                _pause(session, application, actor, f"Could not fill {field.label or field.field_id}.")
                return
            _save_answer(session, application, field, decision, decision.proposed_value, _VALUE_SOURCES[decision.source])
            _record(
                session,
                application,
                "field_filled",
                actor,
                f"Filled {field.label or field.field_id} from {decision.source.value.replace('_', ' ')}.",
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
                blockers.append(field.label or field.field_id)
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
            blockers.append(field.label or field.field_id)
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
            blockers.append(field.label or field.field_id)
    if blockers:
        _pause(session, application, actor, "Waiting for answers to " + ", ".join(blockers) + ".")
        return
    _record(
        session,
        application,
        "page_ready",
        actor,
        "Required fields on this page are filled. The application was not submitted.",
    )


def _open_page(
    session: Session,
    application: Application,
    browser: BrowserManager,
    actor: EventActor,
) -> WorkdayPage | None:
    url = (application.job.apply_url or "").strip()
    if not url:
        _pause(session, application, actor, "This job has no application URL.")
        return None
    try:
        browser.navigate(url)
        page = WorkdayAdapter(browser).read_page()
    except (BrowserError, WorkdayPageError) as exc:
        _pause(session, application, actor, f"Stopped without guessing. {exc}")
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


def _enter_applying(session: Session, application: Application, actor: EventActor, reason: str) -> None:
    if application.status is ApplicationStatus.applying:
        return
    if application.status is not ApplicationStatus.ready_to_apply:
        raise ApplicationTransitionError(
            f"applying starts from ready to apply, not {application.status.value}"
        )
    change_status(session, application, to=ApplicationStatus.applying, actor=actor, reason=reason)
    application.attempts += 1


def _pause(session: Session, application: Application, actor: EventActor, reason: str) -> None:
    cleaned = _clip(reason)
    _record(session, application, "waiting_for_user", actor, cleaned)
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
    existing = session.scalar(
        select(ApplicationAnswer).where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.field_key == field.field_id,
        )
    )
    if existing is None:
        existing = ApplicationAnswer(
            application_id=application.id,
            field_key=field.field_id[:200],
            label=(field.label or field.field_id)[:300],
            field_type=field.type[:32],
            required=field.required,
            canonical_key=decision.canonical_key,
            value=value,
            value_source=source,
            consequential=is_consequential(decision.canonical_key),
            page_index=application.current_page,
        )
        session.add(existing)
    else:
        existing.value = value
        existing.value_source = source
        existing.label = (field.label or field.field_id)[:300]
        existing.required = field.required
        existing.canonical_key = decision.canonical_key
        existing.consequential = is_consequential(decision.canonical_key)
    session.flush()


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


def _latest_inspection(session: Session, application: Application) -> list[dict] | None:
    event = session.scalar(
        select(ApplicationEvent)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.event_type == "page_inspected",
        )
        .order_by(ApplicationEvent.id.desc())
    )
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


def _stored_values(session: Session, application: Application) -> dict[str, str]:
    return {
        answer.field_key: answer.value
        for answer in _answers(session, application)
        if answer.value and answer.value.strip()
    }


def _has_field(page: WorkdayPage, field_id: str) -> bool:
    try:
        page.field(field_id)
    except KeyError:
        return False
    return True


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
