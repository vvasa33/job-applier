"""Decide whether one application may be submitted now. Deterministic, and never twice."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from jobhunter.db.models import Application, ApplicationAnswer, ApplicationEvent, Job
from jobhunter.domain.enums import ApplicationStatus, AutonomyLevel, EventActor, ValueSource
from jobhunter.domain.time import utcnow

AUTO_SOURCES = frozenset({ValueSource.profile, ValueSource.answer_bank, ValueSource.rule})
_APPLIED = (
    ApplicationStatus.submitted,
    ApplicationStatus.interview,
    ApplicationStatus.offer,
    ApplicationStatus.rejected,
)
_CONFIRMATION = re.compile(
    r"(application (was |has been )?(successfully )?(submitted|received))"
    r"|(thank you for (applying|your application|submitting))"
    r"|(we('ve| have) received your application)",
    re.IGNORECASE,
)
_ALREADY_APPLIED = re.compile(r"(you('ve| have) already applied)|(already (submitted|applied) (an application )?(for|to) this)", re.I)


@dataclass(frozen=True)
class SubmitPolicy:
    autonomy: AutonomyLevel = AutonomyLevel.assist
    daily_auto_submit_cap: int = 3
    trusted_after: int = 10


@dataclass(frozen=True)
class SubmitDecision:
    allowed: bool
    needs_confirmation: bool
    reason: str
    automatic: bool = False


def authorize(
    session: Session,
    application: Application,
    *,
    unresolved: list[str],
    fingerprint: str,
    policy: SubmitPolicy,
) -> SubmitDecision:
    if application.submit_intent_at is not None or application.submitted_at is not None:
        return _refuse("A submit was already attempted for this application. It is never tried twice.")
    if application.status is not ApplicationStatus.applying:
        return _refuse(f"Submitting needs an application that is applying, not {application.status.value}.")
    sibling = applied_sibling(session, application.job)
    if sibling is not None:
        return _refuse(f"A duplicate posting (job {sibling}) was already applied to.")
    if unresolved:
        return _refuse("Required fields are not resolved: " + ", ".join(unresolved) + ".")
    if policy.autonomy is AutonomyLevel.observe:
        return _refuse("Observe mode never submits.")
    if confirmed(session, application, fingerprint):
        return SubmitDecision(allowed=True, needs_confirmation=False, reason="You confirmed this submission.")
    if policy.autonomy is AutonomyLevel.supervised:
        blockers = auto_submit_blockers(session, application, policy)
        if not blockers:
            return SubmitDecision(
                allowed=True,
                needs_confirmation=False,
                reason="Every supervised auto-submit condition held.",
                automatic=True,
            )
        return SubmitDecision(
            allowed=False,
            needs_confirmation=True,
            reason="Every required field is resolved. Confirm to submit. Auto-submit was not used: "
            + " ".join(blockers),
        )
    return SubmitDecision(
        allowed=False,
        needs_confirmation=True,
        reason="Every required field is resolved. Confirm to submit.",
    )


def answers_fingerprint(session: Session, application: Application) -> str:
    rows = session.execute(
        select(ApplicationAnswer.field_key, ApplicationAnswer.value)
        .where(ApplicationAnswer.application_id == application.id)
        .order_by(ApplicationAnswer.field_key)
    ).all()
    payload = json.dumps([[key, value or ""] for key, value in rows], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def confirmed(session: Session, application: Application, fingerprint: str) -> bool:
    event = _latest(session, application, "submit_confirmed")
    return event is not None and event.data.get("fingerprint") == fingerprint


def applied_sibling(session: Session, job: Job) -> int | None:
    """Another stored job for the same posting that already has a submitted application."""

    links = [Job.possible_duplicate_of == job.id]
    if job.possible_duplicate_of is not None:
        links.append(Job.id == job.possible_duplicate_of)
        links.append(Job.possible_duplicate_of == job.possible_duplicate_of)
    if job.canonical_apply_url:
        links.append(Job.canonical_apply_url == job.canonical_apply_url)
    row = session.execute(
        select(Job.id)
        .join(Application, Application.job_id == Job.id)
        .where(
            Job.id != job.id,
            or_(*links),
            or_(Application.status.in_(_APPLIED), Application.submit_intent_at.is_not(None)),
        )
        .limit(1)
    ).first()
    return None if row is None else int(row[0])


def auto_submit_blockers(session: Session, application: Application, policy: SubmitPolicy) -> list[str]:
    blockers: list[str] = []
    sources = session.scalars(
        select(ApplicationAnswer.value_source).where(ApplicationAnswer.application_id == application.id)
    ).all()
    if any(source not in AUTO_SOURCES for source in sources):
        blockers.append("Some answers came from you or from an approved draft, not from stored facts.")
    tailored = _latest(session, application, "resume_tailored")
    if tailored is None or tailored.data.get("plan_error"):
        blockers.append("The tailored resume fell back to an unchanged copy or was not checked.")
    trusted = trusted_submissions(session, application.job.ats_type)
    if trusted < policy.trusted_after:
        blockers.append(
            f"Only {trusted} confirmed submissions on {application.job.ats_type or 'this site'}; "
            f"{policy.trusted_after} are needed."
        )
    if auto_submits_since(session, utcnow() - timedelta(days=1)) >= policy.daily_auto_submit_cap:
        blockers.append("The daily auto-submit limit was reached.")
    return blockers


def trusted_submissions(session: Session, ats_type: str | None) -> int:
    confirmed_ids = (
        select(ApplicationEvent.application_id)
        .where(ApplicationEvent.event_type == "submit_confirmed", ApplicationEvent.actor == EventActor.user)
        .scalar_subquery()
    )
    return int(
        session.scalar(
            select(func.count(Application.id))
            .join(Job, Job.id == Application.job_id)
            .where(
                Application.submitted_at.is_not(None),
                Job.ats_type == ats_type,
                Application.id.in_(confirmed_ids),
            )
        )
        or 0
    )


def auto_submits_since(session: Session, since: datetime) -> int:
    events = session.scalars(
        select(ApplicationEvent).where(
            ApplicationEvent.event_type == "submitted",
            ApplicationEvent.created_at >= since,
        )
    ).all()
    return sum(1 for event in events if event.data.get("automatic"))


def confirmation_text(html: str) -> str | None:
    text = visible_text(html)
    match = _CONFIRMATION.search(text)
    if match is None:
        return None
    start = max(0, match.start() - 120)
    return text[start : match.end() + 200].strip()


def already_applied(html: str) -> bool:
    return _ALREADY_APPLIED.search(visible_text(html)) is not None


def visible_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"head", "script", "style", "template"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"head", "script", "style", "template"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data)


def _latest(session: Session, application: Application, event_type: str) -> ApplicationEvent | None:
    return session.scalar(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application.id, ApplicationEvent.event_type == event_type)
        .order_by(ApplicationEvent.id.desc())
    )


def _refuse(reason: str) -> SubmitDecision:
    return SubmitDecision(allowed=False, needs_confirmation=False, reason=reason)
