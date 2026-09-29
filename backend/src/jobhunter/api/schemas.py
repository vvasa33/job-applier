from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from jobhunter.domain.enums import ApplicationStatus


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


class ApplicationResumeResponse(BaseModel):
    application_id: int
    tex_path: str
    pdf_path: str
    sha256: str
    version_id: int


class ResumeVersionOut(BaseModel):
    id: int
    sha256: str
    tex_path: str | None
    pdf_path: str | None
    diff_path: str | None
    created_at: datetime


class ApplicationEventOut(BaseModel):
    id: int
    actor: str
    event_type: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    resume_version_id: int | None
    created_at: datetime


class ApplicationResponse(BaseModel):
    id: int
    job_id: int
    status: str
    allowed_transitions: list[str]
    opened_at: datetime
    status_changed_at: datetime
    started_at: datetime | None
    submitted_at: datetime | None
    resume_version: ResumeVersionOut | None
    resume_versions: list[ResumeVersionOut]
    history: list[ApplicationEventOut]


class OpenApplicationRequest(BaseModel):
    reason: str = "Application opened."


class TransitionRequest(BaseModel):
    to: ApplicationStatus
    reason: str = Field(min_length=1, max_length=500)
    resume_version_id: int | None = None


class DashboardResponse(BaseModel):
    job_count: int
    internship_count: int
    remote_count: int
    application_count: int
    by_status: dict[str, int]
    recent: list[JobSummary]
