import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.db.models import Job
from jobhunter.db.records import JobSighting, record_job_sighting
from jobhunter.domain.enums import CsRelevance, LocationClass
from jobhunter.ingestion.normalize import NormalizationError, NormalizedJob, location_rank, normalize
from jobhunter.ingestion.ports import JobSource
from jobhunter.ingestion.raw import RawJob
from jobhunter.ingestion.registry import SourceRegistry


@dataclass(frozen=True)
class IngestionReport:
    source_key: str
    seen: int
    created: int
    duplicates: int
    rejected: int
    job_ids: tuple[int, ...]
    rejections: tuple[str, ...]


class IngestionService:
    """Validate, normalize, and store jobs from registered sources."""

    def __init__(self, registry: SourceRegistry) -> None:
        self.registry = registry

    def ingest(self, session: Session, source_key: str) -> IngestionReport:
        return _ingest_source(session, self.registry.get(source_key))

    def ingest_all(self, session: Session) -> tuple[IngestionReport, ...]:
        return tuple(_ingest_source(session, source) for source in self.registry.all())


def _ingest_source(session: Session, source: JobSource) -> IngestionReport:
    identity = source.identify()
    created = 0
    duplicates = 0
    rejected = 0
    seen = 0
    job_ids: list[int] = []
    rejections: list[str] = []

    for item in source.discover():
        seen += 1
        try:
            raw = _validate(item)
            detail = source.fetch_detail(raw)
            if detail is not None:
                raw = _validate(detail)
            normalized = normalize(raw, identity)
        except (ValidationError, NormalizationError) as exc:
            rejected += 1
            rejections.append(_rejection_message(item, exc))
            continue

        known_ids = set(session.scalars(select(Job.id)))
        job = record_job_sighting(session, _sighting(normalized))
        _enrich(job, normalized)
        if job.id in known_ids:
            duplicates += 1
        else:
            created += 1
        if job.id not in job_ids:
            job_ids.append(job.id)

    return IngestionReport(
        source_key=identity.key,
        seen=seen,
        created=created,
        duplicates=duplicates,
        rejected=rejected,
        job_ids=tuple(job_ids),
        rejections=tuple(rejections),
    )


def _validate(item: object) -> RawJob:
    if isinstance(item, RawJob):
        return RawJob.model_validate(item.model_dump())
    return RawJob.model_validate(item)


def _sighting(job: NormalizedJob) -> JobSighting:
    raw = dict(job.raw)
    return JobSighting(
        title=job.title,
        company=job.company,
        url=job.url,
        ats_type=job.ats_type,
        external_id=job.external_id,
        board_key=job.board_key,
        requisition_id=job.requisition_id,
        term=job.term,
        description_text=job.description_text,
        locations=job.locations,
        location_class=job.location_class,
        is_internship=job.is_internship,
        cs_relevance=job.cs_relevance,
        posted_at=job.posted_at,
        raw=raw,
        content_hash=_content_hash(raw),
    )


def _enrich(job: Job, normalized: NormalizedJob) -> None:
    locations = list(job.locations or [])
    for location in normalized.locations:
        if location not in locations:
            locations.append(location)
    job.locations = locations
    if location_rank(normalized.location_class) > location_rank(job.location_class):
        job.location_class = normalized.location_class
    if not job.description_text and normalized.description_text:
        job.description_text = normalized.description_text
        job.description_hash = hashlib.sha256(normalized.description_text.encode()).hexdigest()
    if not job.term and normalized.term:
        job.term = normalized.term
    if not job.requisition_id and normalized.requisition_id:
        job.requisition_id = normalized.requisition_id
    if job.is_internship is None and normalized.is_internship is not None:
        job.is_internship = normalized.is_internship
    if job.cs_relevance is CsRelevance.unknown and normalized.cs_relevance is not CsRelevance.unknown:
        job.cs_relevance = normalized.cs_relevance
    if job.location_class is LocationClass.unknown and normalized.location_class is not LocationClass.unknown:
        job.location_class = normalized.location_class


def _content_hash(raw: dict) -> str:
    payload = json.dumps(raw, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _rejection_message(item: object, exc: Exception) -> str:
    external_id = ""
    if isinstance(item, dict):
        external_id = str(item.get("external_id") or "")
    elif isinstance(item, RawJob):
        external_id = item.external_id
    prefix = external_id or "item"
    if isinstance(exc, ValidationError):
        return f"{prefix}: {exc.errors()[0]['msg']}"
    return f"{prefix}: {exc}"
