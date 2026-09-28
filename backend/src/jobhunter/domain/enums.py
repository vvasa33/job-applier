from enum import StrEnum


class JobStatus(StrEnum):
    discovered = "discovered"
    filtered_out = "filtered_out"
    candidate = "candidate"
    scored_low = "scored_low"
    ineligible = "ineligible"
    shortlisted = "shortlisted"
    dismissed = "dismissed"
    application_created = "application_created"
    closed = "closed"


class LocationClass(StrEnum):
    dmv = "dmv"
    us_remote = "us_remote"
    us_other = "us_other"
    non_us = "non_us"
    unknown = "unknown"


class CsRelevance(StrEnum):
    relevant = "relevant"
    unknown = "unknown"
    not_relevant = "not_relevant"


class ApplicationStatus(StrEnum):
    queued = "queued"
    preparing = "preparing"
    ready = "ready"
    in_progress = "in_progress"
    waiting_for_human = "waiting_for_human"
    parked = "parked"
    awaiting_submit_confirmation = "awaiting_submit_confirmation"
    submitting = "submitting"
    submitted = "submitted"
    needs_verification = "needs_verification"
    failed = "failed"
    abandoned = "abandoned"


class ApplicationOutcome(StrEnum):
    none = "none"
    rejected = "rejected"
    oa = "oa"
    interview = "interview"
    offer = "offer"
    withdrawn = "withdrawn"


class AutonomyLevel(StrEnum):
    observe = "observe"
    assist = "assist"
    supervised = "supervised"


class ResumeVersionKind(StrEnum):
    master_snapshot = "master_snapshot"
    tailored = "tailored"


class ValueSource(StrEnum):
    profile = "profile"
    answer_bank = "answer_bank"
    rule = "rule"
    human = "human"
    llm_draft_approved = "llm_draft_approved"


class RequirementKind(StrEnum):
    citizenship_required = "citizenship_required"
    clearance_required = "clearance_required"
    sponsorship_available = "sponsorship_available"
    grad_window = "grad_window"
    cover_letter_required = "cover_letter_required"
    min_gpa = "min_gpa"
    other = "other"


class EventActor(StrEnum):
    system = "system"
    user = "user"
    llm = "llm"


class AgentRunKind(StrEnum):
    discovery = "discovery"
    score = "score"
    prepare = "prepare"
    apply = "apply"
    backup = "backup"


class AgentRunStatus(StrEnum):
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class AgentRunTrigger(StrEnum):
    schedule = "schedule"
    manual = "manual"
