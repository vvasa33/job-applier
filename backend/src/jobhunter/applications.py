"""Open an application and move it through the status graph. The browser is never involved."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.db.models import Application, Job, ResumeVersion
from jobhunter.db.records import create_application, record_application_event
from jobhunter.domain.application_flow import REQUIRES_RESUME_VERSION, require_transition
from jobhunter.domain.enums import ApplicationOutcome, ApplicationStatus, EventActor, JobStatus, ResumeVersionKind
from jobhunter.domain.errors import ApplicationTransitionError
from jobhunter.domain.time import utcnow

_MAX_REASON = 500

_OUTCOMES = {
    ApplicationStatus.rejected: ApplicationOutcome.rejected,
    ApplicationStatus.interview: ApplicationOutcome.interview,
    ApplicationStatus.offer: ApplicationOutcome.offer,
    ApplicationStatus.withdrawn: ApplicationOutcome.withdrawn,
}

_RESULT_STATUSES = frozenset(
    {
        ApplicationStatus.submitted,
        ApplicationStatus.rejected,
        ApplicationStatus.interview,
        ApplicationStatus.offer,
        ApplicationStatus.withdrawn,
    }
)


def open_application(
    session: Session,
    job: Job,
    *,
    actor: EventActor = EventActor.user,
    reason: str = "Application opened.",
) -> Application:
    application = create_application(session, job, actor=actor, reason=_reason(reason))
    if job.status is not JobStatus.closed:
        job.status = JobStatus.application_created
        job.status_reason = None
    session.flush()
    return application


def change_status(
    session: Session,
    application: Application,
    *,
    to: ApplicationStatus,
    actor: EventActor,
    reason: str,
    resume_version_id: int | None = None,
) -> Application:
    cleaned = _reason(reason)
    require_transition(application.status, to)
    version_id = _resume_for_transition(session, application, to, resume_version_id)
    now = utcnow()
    previous = application.status
    application.status = to
    application.status_changed_at = now
    if to is ApplicationStatus.applying and application.started_at is None:
        application.started_at = now
    if to is ApplicationStatus.submitted and application.submitted_at is None:
        application.submitted_at = now
    outcome = _OUTCOMES.get(to)
    if outcome is not None:
        application.outcome = outcome
    if version_id is not None:
        application.resume_version_id = version_id
    record_application_event(
        session,
        application,
        event_type="status_changed",
        actor=actor,
        data={
            "from_status": previous.value,
            "to_status": to.value,
            "reason": cleaned,
            "resume_version_id": application.resume_version_id,
        },
    )
    session.flush()
    return application


def application_for_job(session: Session, job_id: int) -> Application | None:
    return session.scalar(select(Application).where(Application.job_id == job_id))


def _resume_for_transition(
    session: Session,
    application: Application,
    target: ApplicationStatus,
    requested: int | None,
) -> int | None:
    if target in _RESULT_STATUSES and requested not in (None, application.resume_version_id):
        raise ApplicationTransitionError("the resume version cannot change after the application is submitted")
    chosen = requested if requested is not None else application.resume_version_id
    if target in REQUIRES_RESUME_VERSION:
        if chosen is None:
            raise ApplicationTransitionError("ready to apply requires a tailored resume version for this application")
        _require_resume_version(session, application, chosen)
    elif requested is not None:
        _require_resume_version(session, application, requested)
    return requested


def _require_resume_version(session: Session, application: Application, version_id: int) -> None:
    version = session.get(ResumeVersion, version_id)
    if (
        version is None
        or version.application_id != application.id
        or version.kind is not ResumeVersionKind.tailored
    ):
        raise ApplicationTransitionError("resume version must be a tailored resume for this application")


def _reason(reason: str) -> str:
    cleaned = " ".join(reason.split())
    if not cleaned:
        raise ApplicationTransitionError("a transition needs a reason")
    if len(cleaned) > _MAX_REASON:
        raise ApplicationTransitionError(f"a transition reason must be {_MAX_REASON} characters or fewer")
    return cleaned
