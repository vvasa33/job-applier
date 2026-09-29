"""Read-only job queries for the local UI. No scoring and no writes."""

from __future__ import annotations

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from jobhunter.db.models import Application, Job
from jobhunter.domain.enums import ApplicationStatus, JobStatus, LocationClass

WORKPLACES = ("remote", "hybrid", "onsite")


def workplace_of(locations: list[str] | None, location_class: LocationClass) -> str:
    text = " ".join(locations or []).casefold()
    hybrid = "hybrid" in text
    remote = "remote" in text or location_class is LocationClass.us_remote
    if hybrid and remote:
        return "hybrid"
    if hybrid:
        return "hybrid"
    if remote:
        return "remote"
    if locations:
        return "onsite"
    return "unspecified"


def list_jobs(
    session: Session,
    *,
    q: str | None = None,
    internship: bool | None = None,
    location: str | None = None,
    workplace: str | None = None,
    company: str | None = None,
    status: JobStatus | None = None,
    application_status: ApplicationStatus | None = None,
    unapplied: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Job], int]:
    stmt = select(Job).options(selectinload(Job.sources), selectinload(Job.application))
    stmt = _filtered(
        stmt,
        q=q,
        internship=internship,
        location=location,
        workplace=workplace,
        company=company,
        status=status,
        application_status=application_status,
        unapplied=unapplied,
    )
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Job.first_seen_at.desc(), Job.id.desc()).limit(limit).offset(offset)
    ).all()
    return list(rows), int(total)


def job_detail(session: Session, job_id: int) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.sources), selectinload(Job.application), selectinload(Job.requirements))
    )
    return session.scalar(stmt)


def companies(session: Session) -> list[str]:
    rows = session.scalars(select(Job.company_name).distinct().order_by(Job.company_name)).all()
    return list(rows)


def dashboard_counts(session: Session) -> dict[str, int]:
    jobs = session.scalars(select(Job)).all()
    by_status = {status.value: 0 for status in JobStatus}
    internships = 0
    remote = 0
    for job in jobs:
        by_status[job.status.value] = by_status.get(job.status.value, 0) + 1
        if job.is_internship:
            internships += 1
        if workplace_of(job.locations, job.location_class) == "remote":
            remote += 1
    applications = session.scalar(select(func.count()).select_from(Application)) or 0
    return {
        "jobs": len(jobs),
        "internships": internships,
        "remote": remote,
        "applications": int(applications),
        "by_status": by_status,
    }


def _filtered(stmt, **filters):
    q = (filters["q"] or "").strip()
    if q:
        stmt = stmt.where(Job.title.like(_contains(q), escape="\\"))
    if filters["internship"] is not None:
        stmt = stmt.where(Job.is_internship.is_(filters["internship"]))
    location = (filters["location"] or "").strip()
    if location:
        clauses = [cast(Job.locations, String).like(_contains(location), escape="\\")]
        parsed = _location_class(location)
        if parsed is not None:
            clauses.append(Job.location_class == parsed)
        stmt = stmt.where(or_(*clauses))
    workplace = filters["workplace"]
    if workplace:
        loc = cast(Job.locations, String)
        hybrid = loc.like("%hybrid%", escape="\\")
        remote_text = loc.like("%remote%", escape="\\")
        remote = or_(Job.location_class == LocationClass.us_remote, remote_text)
        if workplace == "hybrid":
            stmt = stmt.where(hybrid)
        elif workplace == "remote":
            stmt = stmt.where(remote)
        elif workplace == "onsite":
            stmt = stmt.where(~hybrid, ~remote_text, Job.location_class != LocationClass.us_remote)
    company = (filters["company"] or "").strip()
    if company:
        stmt = stmt.where(Job.company_name == company)
    if filters["status"] is not None:
        stmt = stmt.where(Job.status == filters["status"])
    if filters["unapplied"]:
        stmt = stmt.where(~Job.application.has())
    elif filters["application_status"] is not None:
        stmt = stmt.join(Job.application).where(Application.status == filters["application_status"])
    return stmt


def _contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _location_class(value: str) -> LocationClass | None:
    folded = value.casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "dmv": LocationClass.dmv,
        "us_remote": LocationClass.us_remote,
        "remote": LocationClass.us_remote,
        "us_other": LocationClass.us_other,
        "non_us": LocationClass.non_us,
        "unknown": LocationClass.unknown,
    }
    return aliases.get(folded)
