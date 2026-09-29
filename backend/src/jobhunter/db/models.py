from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jobhunter.db.base import Base
from jobhunter.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    AgentRunTrigger,
    ApplicationOutcome,
    ApplicationStatus,
    AutonomyLevel,
    CsRelevance,
    EventActor,
    JobStatus,
    LocationClass,
    RequirementKind,
    ResumeVersionKind,
    ValueSource,
)
from jobhunter.domain.time import utcnow


def _enum(enum_cls: type, length: int = 40) -> Enum:
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_company_title", "normalized_company", "normalized_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(300), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_company: Mapped[str] = mapped_column(String(200), nullable=False)
    locations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    location_class: Mapped[LocationClass] = mapped_column(
        _enum(LocationClass),
        default=LocationClass.unknown,
        nullable=False,
    )
    is_internship: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cs_relevance: Mapped[CsRelevance] = mapped_column(
        _enum(CsRelevance),
        default=CsRelevance.unknown,
        nullable=False,
    )
    term: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    apply_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    canonical_apply_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    ats_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requisition_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.discovered, nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(700), nullable=False, unique=True)
    possible_duplicate_of: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    sources: Mapped[list[JobSource]] = relationship(back_populates="job")
    requirements: Mapped[list[JobRequirement]] = relationship(back_populates="job")
    application: Mapped[Application | None] = relationship(back_populates="job", uselist=False)


class JobSource(Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        UniqueConstraint("ats_type", "board_key", "external_id", name="uq_job_sources_external"),
        UniqueConstraint("canonical_url", name="uq_job_sources_canonical_url"),
        Index("ix_job_sources_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    ats_type: Mapped[str] = mapped_column(String(40), nullable=False)
    board_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    job: Mapped[Job] = relationship(back_populates="sources")


class JobRequirement(Base):
    __tablename__ = "job_requirements"
    __table_args__ = (
        Index(
            "uq_job_requirements_kind",
            "job_id",
            "kind",
            unique=True,
            sqlite_where=text("kind != 'other'"),
        ),
        Index("ix_job_requirements_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[RequirementKind] = mapped_column(_enum(RequirementKind), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    job: Mapped[Job] = relationship(back_populates="requirements")


class Resume(Base):
    """The master resume. Generated copies live in resume_versions, not here."""

    __tablename__ = "resumes"
    __table_args__ = (
        Index(
            "uq_resumes_one_current",
            "is_current",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(2000), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    structure: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    versions: Mapped[list[ResumeVersion]] = relationship(back_populates="resume")


class Application(TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_applications_job_id"),
        Index("ix_applications_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum(ApplicationStatus, length=40),
        default=ApplicationStatus.found,
        nullable=False,
    )
    autonomy_level: Mapped[AutonomyLevel] = mapped_column(
        _enum(AutonomyLevel),
        default=AutonomyLevel.assist,
        nullable=False,
    )
    outcome: Mapped[ApplicationOutcome] = mapped_column(
        _enum(ApplicationOutcome),
        default=ApplicationOutcome.none,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    job: Mapped[Job] = relationship(back_populates="application")
    events: Mapped[list[ApplicationEvent]] = relationship(back_populates="application")
    answers: Mapped[list[ApplicationAnswer]] = relationship(back_populates="application")
    resume_versions: Mapped[list[ResumeVersion]] = relationship(
        back_populates="application",
        foreign_keys="ResumeVersion.application_id",
    )
    resume_version: Mapped[ResumeVersion | None] = relationship(
        foreign_keys=[resume_version_id],
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'master_snapshot' AND application_id IS NULL) "
            "OR (kind = 'tailored' AND application_id IS NOT NULL)",
            name="ck_resume_versions_application_link",
        ),
        CheckConstraint(
            "kind != 'tailored' OR tex_path IS NOT NULL OR pdf_path IS NOT NULL",
            name="ck_resume_versions_tailored_path",
        ),
        Index("ix_resume_versions_resume_id", "resume_id"),
        Index("ix_resume_versions_application_id", "application_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=False)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[ResumeVersionKind] = mapped_column(_enum(ResumeVersionKind), nullable=False)
    tex_path: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    diff_path: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    resume: Mapped[Resume] = relationship(back_populates="versions")
    application: Mapped[Application | None] = relationship(
        back_populates="resume_versions",
        foreign_keys=[application_id],
    )


class ApplicationEvent(Base):
    """Append-only. Updates and deletes are rejected by database triggers."""

    __tablename__ = "application_events"
    __table_args__ = (
        Index("ix_application_events_application_id_created", "application_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor: Mapped[EventActor] = mapped_column(_enum(EventActor), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    application: Mapped[Application] = relationship(back_populates="events")


class ApplicationAnswer(TimestampMixin, Base):
    __tablename__ = "application_answers"
    __table_args__ = (
        UniqueConstraint("application_id", "field_key", name="uq_application_answers_field"),
        Index("ix_application_answers_application_id", "application_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    field_key: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canonical_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_source: Mapped[ValueSource] = mapped_column(_enum(ValueSource), nullable=False)
    consequential: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    application: Mapped[Application] = relationship(back_populates="answers")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[AgentRunKind] = mapped_column(_enum(AgentRunKind), nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(_enum(AgentRunStatus), nullable=False)
    trigger: Mapped[AgentRunTrigger] = mapped_column(_enum(AgentRunTrigger), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_user_settings_singleton"),
        CheckConstraint("discovery_interval_hours >= 1", name="ck_user_settings_interval"),
        CheckConstraint("daily_llm_budget_usd >= 0", name="ck_user_settings_budget"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    autonomy_level: Mapped[AutonomyLevel] = mapped_column(_enum(AutonomyLevel), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discovery_interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_llm_budget_usd: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
