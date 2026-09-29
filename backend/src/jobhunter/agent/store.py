"""Agent state, activity log, and bounded retries in SQLite, shared by the worker and the API."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobhunter.db.models import AgentActivity, AgentAttempt, AgentState
from jobhunter.domain.enums import AgentDesired, AgentPhase
from jobhunter.domain.time import utcnow

HEARTBEAT_STALE = timedelta(seconds=15)
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))


def state(session: Session) -> AgentState:
    row = session.get(AgentState, 1)
    if row is None:
        row = AgentState(id=1, desired=AgentDesired.stopped, phase=AgentPhase.stopped)
        session.add(row)
        session.flush()
    return row


def set_desired(session: Session, desired: AgentDesired) -> AgentState:
    row = state(session)
    row.desired = desired
    row.updated_at = utcnow()
    log(session, "info", "agent_" + ("start" if desired is AgentDesired.running else "stop") + "_requested",
        "Start requested." if desired is AgentDesired.running else "Stop requested.")
    return row


def worker_alive(row: AgentState, now: datetime | None = None) -> bool:
    if row.pid is None or row.heartbeat_at is None:
        return False
    return (now or utcnow()) - aware(row.heartbeat_at) < HEARTBEAT_STALE


def log(
    session: Session,
    level: str,
    kind: str,
    message: str,
    *,
    job_id: int | None = None,
    application_id: int | None = None,
    **data: object,
) -> AgentActivity:
    entry = AgentActivity(
        level=level,
        kind=kind[:64],
        message=" ".join(message.split())[:1000] or kind,
        job_id=job_id,
        application_id=application_id,
        data=dict(data),
    )
    session.add(entry)
    session.flush()
    return entry


def activity(session: Session, limit: int = 100) -> list[AgentActivity]:
    return list(session.scalars(select(AgentActivity).order_by(AgentActivity.id.desc()).limit(limit)).all())


def count_activity(session: Session, kind: str, since: datetime) -> int:
    return int(
        session.scalar(
            select(func.count(AgentActivity.id)).where(AgentActivity.kind == kind, AgentActivity.created_at >= since)
        )
        or 0
    )


def last_activity(session: Session, kind: str) -> AgentActivity | None:
    return session.scalar(select(AgentActivity).where(AgentActivity.kind == kind).order_by(AgentActivity.id.desc()))


def attempt(session: Session, stage: str, subject_id: int) -> AgentAttempt | None:
    return session.scalar(
        select(AgentAttempt).where(AgentAttempt.stage == stage, AgentAttempt.subject_id == subject_id)
    )


def available(session: Session, stage: str, subject_id: int, now: datetime | None = None) -> bool:
    row = attempt(session, stage, subject_id)
    if row is None:
        return True
    if row.gave_up:
        return False
    return row.next_attempt_at is None or aware(row.next_attempt_at) <= (now or utcnow())


def record_failure(
    session: Session,
    stage: str,
    subject_id: int,
    error: str,
    *,
    max_failures: int,
    now: datetime | None = None,
) -> AgentAttempt:
    """Count one failure. After `max_failures` the subject is never retried automatically."""

    moment = now or utcnow()
    row = attempt(session, stage, subject_id)
    if row is None:
        row = AgentAttempt(stage=stage, subject_id=subject_id, failures=0, gave_up=False)
        session.add(row)
    row.failures += 1
    row.last_error = error[:4000]
    row.updated_at = moment
    if row.failures >= max_failures:
        row.gave_up = True
        row.next_attempt_at = None
    else:
        row.next_attempt_at = moment + BACKOFF[min(row.failures - 1, len(BACKOFF) - 1)]
    session.flush()
    return row


def count_run(session: Session, stage: str, subject_id: int) -> int:
    """Count an attempt that did not fail but still used a try, such as replaying an interrupted page walk."""

    row = attempt(session, stage, subject_id)
    if row is None:
        row = AgentAttempt(stage=stage, subject_id=subject_id, failures=0, gave_up=False)
        session.add(row)
    row.failures += 1
    row.updated_at = utcnow()
    session.flush()
    return row.failures


def clear(session: Session, stage: str, subject_id: int) -> None:
    row = attempt(session, stage, subject_id)
    if row is not None:
        session.delete(row)
        session.flush()


def aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
