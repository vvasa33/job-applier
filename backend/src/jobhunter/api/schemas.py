from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str


class JobSummary(BaseModel):
    id: int
    title: str
    company: str
    locations: list[str]
    location_class: str
    workplace: str
    sources: list[str]
    first_seen_at: datetime
    is_internship: bool | None
    status: str
    application_status: str | None
    ats_type: str | None


class JobSourceOut(BaseModel):
    ats_type: str
    board_key: str
    external_id: str
    url: str
    title: str
    first_seen_at: datetime
    last_seen_at: datetime


class RequirementOut(BaseModel):
    kind: str
    value: str
    verified: bool


class JobDetail(JobSummary):
    normalized_title: str
    normalized_company: str
    cs_relevance: str
    term: str | None
    description_text: str | None
    apply_url: str | None
    canonical_apply_url: str | None
    requisition_id: str | None
    posted_at: datetime | None
    closed_at: datetime | None
    status_reason: str | None
    dedup_key: str
    possible_duplicate_of: int | None
    source_records: list[JobSourceOut]
    requirements: list[RequirementOut]


class JobListResponse(BaseModel):
    total: int
    companies: list[str]
    jobs: list[JobSummary]


class ResumeSectionOut(BaseModel):
    name: str
    kind: str
    entries: list[str]


class ValidateResponse(BaseModel):
    path: str
    sha256: str
    resume_id: int
    sections: list[ResumeSectionOut]


class CompileResponse(BaseModel):
    path: str
    sha256: str
    pdf_path: str


class DashboardResponse(BaseModel):
    job_count: int
    internship_count: int
    remote_count: int
    application_count: int
    by_status: dict[str, int]
    recent: list[JobSummary]
