from datetime import datetime
from decimal import Decimal
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
    ai_cost_usd: float = 0


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
    ai_cost_usd: float = 0


class ApplicationEventOut(BaseModel):
    id: int
    actor: str
    event_type: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    resume_version_id: int | None
    created_at: datetime


class PendingFieldOut(BaseModel):
    field_id: str
    label: str
    action: str
    proposed_value: str | None = None
    reasoning: str
    required: bool = False
    confidence: float = 0
    type: str = ""
    options: list[str] = Field(default_factory=list)


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
    pending_fields: list[PendingFieldOut] = Field(default_factory=list)
    company: str = ""
    title: str = ""
    page_url: str | None = None
    page_title: str | None = None
    has_screenshot: bool = False
    waiting_reason: str | None = None
    pause_kind: str | None = None
    resume_queued: bool = False
    submit_attempted: bool = False
    agent_owns_browser: bool = False
    ai_cost_usd: float = 0


class ReviewQueueItem(BaseModel):
    application_id: int
    job_id: int
    company: str
    title: str
    waiting_since: datetime
    question_count: int
    question_label: str | None = None
    waiting_reason: str | None = None
    has_screenshot: bool = False
    pause_kind: str | None = None
    resume_queued: bool = False


class ApplicantFactIn(BaseModel):
    key: str
    value: str
    confidence: float = Field(default=1, ge=0, le=1)
    source: str = "profile"


class RunApplicationRequest(BaseModel):
    facts: list[ApplicantFactIn] = Field(default_factory=list)
    resume_path: str | None = None
    resume_text: str | None = None


class ResumeApplicationRequest(BaseModel):
    answers: dict[str, str] = Field(min_length=1)
    facts: list[ApplicantFactIn] = Field(default_factory=list)
    resume_path: str | None = None
    resume_text: str | None = None


class ReviewRequest(BaseModel):
    action: Literal["approve", "edit", "skip", "stop", "continue", "confirm_submit", "confirm_submitted"]
    field_id: str = ""
    value: str | None = None
    facts: list[ApplicantFactIn] = Field(default_factory=list)
    resume_path: str | None = None
    resume_text: str | None = None


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


class AgentLimitsOut(BaseModel):
    daily_applications: int
    applications_today: int
    daily_auto_submits: int
    apply_interval_seconds: int
    max_failures: int


class AgentCurrentOut(BaseModel):
    job_id: int | None
    application_id: int | None
    company: str | None
    title: str | None
    step: str | None


class AIDayOut(BaseModel):
    day: str
    cost_usd: float
    saved_usd: float
    calls: int
    cached: int
    refused: int


class AIStatusOut(BaseModel):
    configured: bool
    cheap_model: str
    strong_model: str
    day: str
    spent_today_usd: float
    budget_usd: float
    exceeded: bool
    by_purpose: dict[str, float]
    days: list[AIDayOut]


class AgentStatusResponse(BaseModel):
    desired: str
    phase: str
    alive: bool
    pid: int | None
    started_at: datetime | None
    heartbeat_at: datetime | None
    last_discovery_at: datetime | None
    next_discovery_at: datetime | None
    current: AgentCurrentOut
    message: str | None
    autonomy_level: str
    discovery_interval_hours: int
    waiting_for_user: int
    submitted_today: int
    failing: int
    gave_up: int
    sources_configured: int
    sources_error: str | None
    limits: AgentLimitsOut
    ai: AIStatusOut


class AgentSettingsRequest(BaseModel):
    autonomy_level: Literal["observe", "assist", "supervised"] | None = None
    discovery_interval_hours: int | None = Field(default=None, ge=1, le=168)
    daily_llm_budget_usd: Decimal | None = Field(default=None, ge=0, le=1000, decimal_places=2)


class AgentActivityOut(BaseModel):
    id: int
    created_at: datetime
    level: str
    kind: str
    message: str
    job_id: int | None
    application_id: int | None
