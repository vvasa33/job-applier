"""Core domain tables.

Revision ID: 0001_core_domain
Revises:
Create Date: 2026-09-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_core_domain"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("normalized_title", sa.String(300), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("normalized_company", sa.String(200), nullable=False),
        sa.Column("locations", sa.JSON(), nullable=False),
        sa.Column("location_class", sa.String(40), nullable=False),
        sa.Column("is_internship", sa.Boolean(), nullable=True),
        sa.Column("cs_relevance", sa.String(40), nullable=False),
        sa.Column("term", sa.String(80), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("description_hash", sa.String(64), nullable=True),
        sa.Column("apply_url", sa.String(2000), nullable=True),
        sa.Column("canonical_apply_url", sa.String(2000), nullable=True),
        sa.Column("ats_type", sa.String(40), nullable=True),
        sa.Column("requisition_id", sa.String(80), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("status_reason", sa.String(300), nullable=True),
        sa.Column("dedup_key", sa.String(700), nullable=False),
        sa.Column("possible_duplicate_of", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["possible_duplicate_of"], ["jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("dedup_key", name="uq_jobs_dedup_key"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_company_title", "jobs", ["normalized_company", "normalized_title"])

    op.create_table(
        "job_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("ats_type", sa.String(40), nullable=False),
        sa.Column("board_key", sa.String(200), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("canonical_url", sa.String(2000), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("ats_type", "board_key", "external_id", name="uq_job_sources_external"),
        sa.UniqueConstraint("canonical_url", name="uq_job_sources_canonical_url"),
    )
    op.create_index("ix_job_sources_job_id", "job_sources", ["job_id"])

    op.create_table(
        "job_requirements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_job_requirements_job_id", "job_requirements", ["job_id"])
    op.create_index(
        "uq_job_requirements_kind",
        "job_requirements",
        ["job_id", "kind"],
        unique=True,
        sqlite_where=sa.text("kind != 'other'"),
    )

    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(2000), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_resumes_sha256"),
    )
    op.create_index(
        "uq_resumes_one_current",
        "resumes",
        ["is_current"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("autonomy_level", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("current_page", sa.Integer(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_intent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", name="uq_applications_job_id"),
    )
    op.create_index("ix_applications_status", "applications", ["status"])

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resume_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("tex_path", sa.String(2000), nullable=True),
        sa.Column("pdf_path", sa.String(2000), nullable=True),
        sa.Column("diff_path", sa.String(2000), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(kind = 'master_snapshot' AND application_id IS NULL) "
            "OR (kind = 'tailored' AND application_id IS NOT NULL)",
            name="ck_resume_versions_application_link",
        ),
        sa.CheckConstraint(
            "kind != 'tailored' OR tex_path IS NOT NULL OR pdf_path IS NOT NULL",
            name="ck_resume_versions_tailored_path",
        ),
    )
    op.create_index("ix_resume_versions_resume_id", "resume_versions", ["resume_id"])
    op.create_index("ix_resume_versions_application_id", "resume_versions", ["application_id"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_application_events_application_id_created",
        "application_events",
        ["application_id", "created_at"],
    )
    op.execute(
        """
        CREATE TRIGGER application_events_append_only_update
        BEFORE UPDATE ON application_events
        BEGIN
            SELECT RAISE(ABORT, 'application_events is append-only');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER application_events_append_only_delete
        BEFORE DELETE ON application_events
        BEGIN
            SELECT RAISE(ABORT, 'application_events is append-only');
        END
        """
    )

    op.create_table(
        "application_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("field_key", sa.String(200), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("field_type", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("canonical_key", sa.String(120), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("value_source", sa.String(40), nullable=False),
        sa.Column("consequential", sa.Boolean(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("application_id", "field_key", name="uq_application_answers_field"),
    )
    op.create_index("ix_application_answers_application_id", "application_answers", ["application_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_runs_idempotency_key"),
    )
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("autonomy_level", sa.String(40), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("discovery_interval_hours", sa.Integer(), nullable=False),
        sa.Column("daily_llm_budget_usd", sa.Numeric(8, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_user_settings_singleton"),
        sa.CheckConstraint("discovery_interval_hours >= 1", name="ck_user_settings_interval"),
        sa.CheckConstraint("daily_llm_budget_usd >= 0", name="ck_user_settings_budget"),
    )
    op.execute(
        """
        INSERT INTO user_settings (
            id, autonomy_level, paused, discovery_interval_hours,
            daily_llm_budget_usd, created_at, updated_at
        ) VALUES (
            1, 'assist', 0, 3, 2.00, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS application_events_append_only_delete")
    op.execute("DROP TRIGGER IF EXISTS application_events_append_only_update")
    op.drop_table("user_settings")
    op.drop_table("agent_runs")
    op.drop_table("application_answers")
    op.drop_table("application_events")
    op.drop_table("resume_versions")
    op.drop_table("applications")
    op.drop_table("resumes")
    op.drop_table("job_requirements")
    op.drop_table("job_sources")
    op.drop_table("jobs")
