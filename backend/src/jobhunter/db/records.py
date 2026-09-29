import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.db.models import Application, ApplicationEvent, Job, JobSource, Resume, ResumeVersion
from jobhunter.domain.enums import (
    ApplicationStatus,
    AutonomyLevel,
    CsRelevance,
    EventActor,
    JobStatus,
    LocationClass,
    ResumeVersionKind,
)
from jobhunter.domain.errors import DuplicateApplication, ResumeVersionError
from jobhunter.domain.identity import canonical_url, make_dedup_key, normalize_name
from jobhunter.domain.time import utcnow


@dataclass(frozen=True)
class JobSighting:
    title: str
    company: str
    url: str
    ats_type: str
    external_id: str
    board_key: str = ""
    requisition_id: str | None = None
    term: str | None = None
    description_text: str | None = None
    locations: tuple[str, ...] = ()
    location_class: LocationClass = LocationClass.unknown
    is_internship: bool | None = None
    cs_relevance: CsRelevance = CsRelevance.unknown
    posted_at: datetime | None = None
    raw: dict | None = None
    content_hash: str | None = None


def record_job_sighting(session: Session, sighting: JobSighting) -> Job:
    """Insert a canonical job, or attach this sighting to the job that already exists."""
    key = make_dedup_key(
        company=sighting.company,
        requisition_id=sighting.requisition_id,
        apply_url=sighting.url,
        title=sighting.title,
        term=sighting.term,
    )
    normalized_url = canonical_url(sighting.url)
    job = session.scalar(select(Job).where(Job.dedup_key == key))
    if job is None:
        job = _job_for_existing_source(session, sighting, normalized_url)
    if job is None:
        now = utcnow()
        job = Job(
            title=sighting.title,
            normalized_title=normalize_name(sighting.title),
            company_name=sighting.company,
            normalized_company=normalize_name(sighting.company),
            locations=list(sighting.locations),
            location_class=sighting.location_class,
            is_internship=sighting.is_internship,
            cs_relevance=sighting.cs_relevance,
            term=sighting.term,
            description_text=sighting.description_text,
            apply_url=sighting.url,
            canonical_apply_url=normalized_url,
            ats_type=sighting.ats_type,
            requisition_id=(sighting.requisition_id or "").strip() or None,
            posted_at=sighting.posted_at,
            description_hash=_sha256(sighting.description_text) if sighting.description_text else None,
            first_seen_at=now,
            status=JobStatus.discovered,
            dedup_key=key,
        )
        session.add(job)
        session.flush()

    _attach_source(session, job, sighting, normalized_url)
    session.flush()
    return job


def create_application(
    session: Session,
    job: Job,
    *,
    autonomy_level: AutonomyLevel = AutonomyLevel.assist,
) -> Application:
    existing_id = session.scalar(select(Application.id).where(Application.job_id == job.id))
    if existing_id is not None:
        raise DuplicateApplication(f"job {job.id} already has application {existing_id}")
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.queued,
        autonomy_level=autonomy_level,
    )
    session.add(application)
    session.flush()
    return application


def record_application_event(
    session: Session,
    application: Application,
    *,
    event_type: str,
    actor: EventActor,
    data: dict | None = None,
) -> ApplicationEvent:
    event = ApplicationEvent(
        application_id=application.id,
        event_type=event_type,
        actor=actor,
        data=dict(data or {}),
    )
    session.add(event)
    session.flush()
    return event


def add_master_resume(
    session: Session,
    *,
    path: str,
    sha256: str,
    structure: dict | None = None,
) -> Resume:
    current = session.scalars(select(Resume).where(Resume.is_current.is_(True))).all()
    existing = session.scalar(select(Resume).where(Resume.sha256 == sha256))
    if existing is not None:
        for resume in current:
            if resume.id != existing.id:
                resume.is_current = False
        existing.path = path
        existing.is_current = True
        if structure is not None:
            existing.structure = structure
        session.flush()
        return existing
    for resume in current:
        resume.is_current = False
    resume = Resume(path=path, sha256=sha256, is_current=True, structure=structure)
    session.add(resume)
    session.flush()
    return resume


def add_resume_version(
    session: Session,
    *,
    resume: Resume,
    kind: ResumeVersionKind,
    sha256: str,
    application: Application | None = None,
    tex_path: str | None = None,
    pdf_path: str | None = None,
    diff_path: str | None = None,
) -> ResumeVersion:
    if kind is ResumeVersionKind.master_snapshot and application is not None:
        raise ResumeVersionError("a master snapshot is not tied to an application")
    if kind is ResumeVersionKind.tailored and application is None:
        raise ResumeVersionError("a tailored resume version requires an application")
    if kind is ResumeVersionKind.tailored and not tex_path and not pdf_path:
        raise ResumeVersionError("a tailored resume version requires a filesystem path")
    version = ResumeVersion(
        resume_id=resume.id,
        application_id=None if application is None else application.id,
        kind=kind,
        tex_path=tex_path,
        pdf_path=pdf_path,
        diff_path=diff_path,
        sha256=sha256,
    )
    session.add(version)
    session.flush()
    return version


def _job_for_existing_source(session: Session, sighting: JobSighting, normalized_url: str) -> Job | None:
    source = session.scalar(
        select(JobSource).where(
            JobSource.ats_type == sighting.ats_type,
            JobSource.board_key == sighting.board_key,
            JobSource.external_id == sighting.external_id,
        )
    )
    if source is None:
        source = session.scalar(select(JobSource).where(JobSource.canonical_url == normalized_url))
    if source is None:
        return None
    return session.get(Job, source.job_id)


def _attach_source(session: Session, job: Job, sighting: JobSighting, normalized_url: str) -> None:
    source = session.scalar(
        select(JobSource).where(
            JobSource.ats_type == sighting.ats_type,
            JobSource.board_key == sighting.board_key,
            JobSource.external_id == sighting.external_id,
        )
    )
    if source is None:
        source = session.scalar(select(JobSource).where(JobSource.canonical_url == normalized_url))
    now = utcnow()
    if source is None:
        session.add(
            JobSource(
                job_id=job.id,
                ats_type=sighting.ats_type,
                board_key=sighting.board_key,
                external_id=sighting.external_id,
                url=sighting.url,
                canonical_url=normalized_url,
                title=sighting.title,
                raw=sighting.raw,
                content_hash=sighting.content_hash,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        return
    source.last_seen_at = now
    source.title = sighting.title
    source.url = sighting.url
    if sighting.raw is not None:
        source.raw = sighting.raw
        source.content_hash = sighting.content_hash


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
