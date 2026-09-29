"""One unit of agent work at a time. Each unit is isolated, so one broken job cannot stop the rest."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.agent import store
from jobhunter.agent.config import AgentConfigError, applicant_for, load_sources, submit_policy, user_settings
from jobhunter.applications import change_status, open_application
from jobhunter.apply.runner import pause_application, recover_interrupted_submit, resume_ready, run_page
from jobhunter.apply.submission import applied_sibling
from jobhunter.browser.manager import BrowserManager
from jobhunter.config import Settings
from jobhunter.db.models import Application, ApplicationEvent, Job, ResumeVersion
from jobhunter.db.records import record_application_event
from jobhunter.dedup.service import DedupService
from jobhunter.domain.enums import (
    AgentPhase,
    ApplicationStatus,
    AutonomyLevel,
    EventActor,
    JobStatus,
    ResumeVersionKind,
)
from jobhunter.domain.errors import DuplicateApplication
from jobhunter.domain.time import utcnow
from jobhunter.ingestion.ports import JobSource
from jobhunter.ingestion.registry import SourceRegistry
from jobhunter.ingestion.service import IngestionService
from jobhunter.matching.profiles import ResumeProfile
from jobhunter.matching.service import match_job
from jobhunter.resume.tailoring import job_profile

APPLY_ADAPTERS = frozenset({"workday"})
_PREPARING = (
    ApplicationStatus.found,
    ApplicationStatus.matched,
    ApplicationStatus.saved,
    ApplicationStatus.tailoring,
)
_WALK_TO_TAILORING = {
    ApplicationStatus.found: ApplicationStatus.matched,
    ApplicationStatus.matched: ApplicationStatus.saved,
    ApplicationStatus.saved: ApplicationStatus.tailoring,
}


@dataclass(frozen=True)
class ResumeMaterial:
    profile: ResumeProfile
    text: str


@dataclass(frozen=True)
class TailorResult:
    version_id: int
    plan_error: str | None


@dataclass
class AgentDeps:
    settings: Settings
    sources: Callable[[], list[JobSource]]
    resume: Callable[[Session], ResumeMaterial | None]
    tailor: Callable[[Session, Application], TailorResult]
    browser: Callable[[], BrowserManager]
    now: Callable[[], datetime] = field(default=utcnow)


Report = Callable[..., None]


class Pipeline:
    def __init__(
        self,
        deps: AgentDeps,
        session_factory: Callable[[], Session],
        *,
        report: Report,
        cancel: Callable[[], bool],
    ) -> None:
        self.deps = deps
        self.settings = deps.settings
        self._sessions = session_factory
        self._report = report
        self._cancel = cancel
        self._browser: BrowserManager | None = None

    @property
    def browser_open(self) -> bool:
        return self._browser is not None

    def close_browser(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    def recover(self) -> None:
        """Applications left mid-submit are never retried; they wait for the user to check."""

        with self._sessions() as session:
            rows = session.scalars(
                select(Application).where(
                    Application.status == ApplicationStatus.applying,
                    Application.submit_intent_at.is_not(None),
                    Application.submitted_at.is_(None),
                )
            ).all()
            for application in rows:
                if recover_interrupted_submit(session, application, EventActor.system):
                    store.log(
                        session,
                        "warning",
                        "submit_unverified",
                        "A submit was interrupted. It will not be tried again; check whether it went through.",
                        job_id=application.job_id,
                        application_id=application.id,
                    )
            session.commit()

    def next_unit(self) -> bool:
        for unit in (
            self._resume_waiting,
            self._continue_interrupted,
            self._start_ready,
            self._prepare_one,
            self._discover_if_due,
        ):
            if self._cancel():
                return False
            if unit():
                return True
        return False

    def waiting_count(self) -> int:
        with self._sessions() as session:
            return len(
                session.scalars(
                    select(Application.id).where(Application.status == ApplicationStatus.waiting_for_user)
                ).all()
            )

    def _resume_waiting(self) -> bool:
        with self._sessions() as session:
            candidates = session.scalars(
                select(Application)
                .where(Application.status == ApplicationStatus.waiting_for_user)
                .order_by(Application.status_changed_at, Application.id)
            ).all()
            chosen = next(
                (
                    item.id
                    for item in candidates
                    if resume_ready(session, item)
                    and _can_drive(item.job)
                    and store.available(session, "apply", item.id, self.deps.now())
                ),
                None,
            )
        if chosen is None:
            return False
        self._apply(chosen, "Continuing after your input.")
        return True

    def _continue_interrupted(self) -> bool:
        with self._sessions() as session:
            rows = session.scalars(
                select(Application)
                .where(Application.status == ApplicationStatus.applying, Application.submit_intent_at.is_(None))
                .order_by(Application.status_changed_at, Application.id)
            ).all()
            chosen = next(
                (item for item in rows if _can_drive(item.job) and store.available(session, "apply", item.id, self.deps.now())),
                None,
            )
            if chosen is None:
                return False
            replays = store.count_run(session, "replay", chosen.id)
            if replays > self.settings.agent_max_replays:
                pause_application(
                    session,
                    chosen,
                    EventActor.system,
                    f"The agent reopened this application {replays - 1} times without finishing. "
                    "It stopped so it does not loop; continue it from the review queue.",
                )
                store.clear(session, "replay", chosen.id)
                store.log(
                    session,
                    "warning",
                    "replay_limit",
                    "Stopped reopening an application that kept getting interrupted.",
                    job_id=chosen.job_id,
                    application_id=chosen.id,
                )
                session.commit()
                return True
            session.commit()
            chosen_id = chosen.id
        self._apply(chosen_id, "Reopening an application that was interrupted.")
        return True

    def _start_ready(self) -> bool:
        with self._sessions() as session:
            if user_settings(session).autonomy_level is AutonomyLevel.observe:
                return False
            rows = session.scalars(
                select(Application)
                .where(Application.status == ApplicationStatus.ready_to_apply)
                .order_by(Application.status_changed_at, Application.id)
            ).all()
            chosen = next(
                (item.id for item in rows if _can_drive(item.job) and store.available(session, "apply", item.id, self.deps.now())),
                None,
            )
            if chosen is None or not self._apply_allowed(session):
                return False
        self._apply(chosen, "Starting the application.")
        return True

    def _apply_allowed(self, session: Session) -> bool:
        now = self.deps.now()
        if store.count_activity(session, "apply_started", now - timedelta(days=1)) >= self.settings.agent_daily_applications:
            return False
        last = store.last_activity(session, "apply_started")
        if last is None:
            return True
        return now - store.aware(last.created_at) >= timedelta(seconds=self.settings.agent_apply_interval_seconds)

    def _apply(self, application_id: int, step: str) -> None:
        def work(session: Session) -> None:
            application = session.get(Application, application_id)
            if application is None:
                return
            self._report(AgentPhase.applying, step, job_id=application.job_id, application_id=application.id)
            if application.status is ApplicationStatus.ready_to_apply:
                store.log(
                    session,
                    "info",
                    "apply_started",
                    f"Opening the application for {application.job.title} at {application.job.company_name}.",
                    job_id=application.job_id,
                    application_id=application.id,
                )
                session.commit()
            if self._browser is None:
                self._browser = self.deps.browser()
            material = self.deps.resume(session)
            run_page(
                session,
                application,
                self._browser,
                applicant_for(self.settings, application, resume_text=None if material is None else material.text),
                actor=EventActor.system,
                screenshot_dir=self.settings.data_dir / "screenshots",
                policy=submit_policy(session, self.settings),
                cancel=self._cancel,
            )
            outcome = application.status
            if outcome is ApplicationStatus.submitted:
                store.log(
                    session,
                    "info",
                    "submitted",
                    f"Submitted {application.job.title} at {application.job.company_name}.",
                    job_id=application.job_id,
                    application_id=application.id,
                )
                store.clear(session, "replay", application.id)
            elif outcome is ApplicationStatus.waiting_for_user:
                store.log(
                    session,
                    "info",
                    "waiting_for_user",
                    f"{application.job.company_name}: {_latest_reason(session, application)}",
                    job_id=application.job_id,
                    application_id=application.id,
                )
                store.clear(session, "replay", application.id)
            session.commit()
            store_clear_failures(session, "apply", application.id)

        def gave_up(session: Session, error: str) -> None:
            application = session.get(Application, application_id)
            if application is not None and application.status is ApplicationStatus.applying and application.submit_intent_at is None:
                pause_application(
                    session,
                    application,
                    EventActor.system,
                    f"The agent stopped retrying after {self.settings.agent_max_failures} failures. Last error: {error}",
                )

        self._isolated("apply", application_id, work, gave_up=gave_up, browser_stage=True)

    def _prepare_one(self) -> bool:
        with self._sessions() as session:
            if user_settings(session).autonomy_level is AutonomyLevel.observe:
                return False
            rows = session.scalars(
                select(Application)
                .where(Application.status.in_(_PREPARING))
                .order_by(Application.queued_at, Application.id)
            ).all()
            chosen = next(
                (
                    item.id
                    for item in rows
                    if _agent_created(session, item) and store.available(session, "prepare", item.id, self.deps.now())
                ),
                None,
            )
        if chosen is None:
            return False

        def work(session: Session) -> None:
            application = session.get(Application, chosen)
            if application is None:
                return
            job = application.job
            self._report(
                AgentPhase.preparing,
                f"Tailoring a resume for {job.title} at {job.company_name}.",
                job_id=job.id,
                application_id=application.id,
            )
            while application.status in _WALK_TO_TAILORING:
                change_status(
                    session,
                    application,
                    to=_WALK_TO_TAILORING[application.status],
                    actor=EventActor.system,
                    reason="Moved forward by the agent to tailor a resume.",
                )
            session.commit()
            version_id = _tailored_version(session, application)
            plan_error = None
            if version_id is None:
                result = self.deps.tailor(session, application)
                version_id, plan_error = result.version_id, result.plan_error
                record_application_event(
                    session,
                    application,
                    event_type="resume_tailored",
                    actor=EventActor.system,
                    data={
                        "reason": "Tailored a resume copy."
                        if plan_error is None
                        else f"Used an unchanged resume copy: {plan_error}"[:500],
                        "resume_version_id": version_id,
                        "plan_error": plan_error,
                    },
                )
            change_status(
                session,
                application,
                to=ApplicationStatus.ready_to_apply,
                actor=EventActor.system,
                reason="The tailored resume is ready.",
                resume_version_id=version_id,
            )
            store.log(
                session,
                "info" if plan_error is None else "warning",
                "prepared",
                f"Resume ready for {job.title} at {job.company_name}."
                + ("" if plan_error is None else f" The copy is unchanged: {plan_error}"),
                job_id=job.id,
                application_id=application.id,
            )
            session.commit()

        self._isolated("prepare", chosen, work)
        return True

    def _discover_if_due(self) -> bool:
        now = self.deps.now()
        with self._sessions() as session:
            row = store.state(session)
            due = row.next_discovery_at is None or store.aware(row.next_discovery_at) <= now
            interval = timedelta(hours=user_settings(session).discovery_interval_hours)
            if not due:
                return False
            row.last_discovery_at = now
            row.next_discovery_at = now + interval
            session.commit()
        self._report(AgentPhase.discovering, "Discovering new jobs.")
        try:
            self._discover()
        except Exception as exc:
            with self._sessions() as session:
                store.log(session, "error", "discovery_failed", f"Discovery stopped early: {exc}")
                session.commit()
        return True

    def _discover(self) -> None:
        try:
            sources = self.deps.sources()
        except AgentConfigError as exc:
            with self._sessions() as session:
                store.log(session, "error", "sources_invalid", str(exc))
                session.commit()
            sources = []
        created_total = 0
        for source in sources:
            if self._cancel():
                return
            identity = source.identify()
            self._report(AgentPhase.discovering, f"Checking {identity.label}.")
            registry = SourceRegistry()
            registry.register(source)
            with self._sessions() as session:
                try:
                    report = IngestionService(registry).ingest(session, identity.key)
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    store.log(session, "warning", "source_failed", f"{identity.label} failed and was skipped: {exc}")
                    session.commit()
                    continue
                created_total += report.created
                store.log(
                    session,
                    "info",
                    "source_checked",
                    f"{identity.label}: {report.seen} seen, {report.created} new, "
                    f"{report.duplicates} already known, {report.rejected} rejected.",
                )
                session.commit()
        with self._sessions() as session:
            try:
                merged = DedupService().deduplicate(session)
                session.commit()
                if merged.merges:
                    store.log(session, "info", "deduplicated", f"Merged {len(merged.merges)} duplicate postings.")
                    session.commit()
            except Exception as exc:
                session.rollback()
                store.log(session, "warning", "dedup_failed", f"Deduplication failed and was skipped: {exc}")
                session.commit()
        self._triage()

    def _triage(self) -> None:
        with self._sessions() as session:
            material = self.deps.resume(session)
            if material is None:
                store.log(
                    session,
                    "warning",
                    "resume_missing",
                    "The master resume is not configured or not valid, so new jobs were not matched.",
                )
                session.commit()
                return
            job_ids = session.scalars(
                select(Job.id).where(Job.status == JobStatus.discovered).order_by(Job.first_seen_at, Job.id)
            ).all()
        counts = {"shortlisted": 0, "scored_low": 0, "filtered_out": 0}
        for job_id in job_ids:
            if self._cancel():
                return
            with self._sessions() as session:
                if not store.available(session, "triage", job_id, self.deps.now()):
                    continue
                job = session.get(Job, job_id)
                if job is None or job.application is not None:
                    continue
                try:
                    match = match_job(job_profile(job), material.profile)
                except Exception as exc:
                    session.rollback()
                    store.record_failure(session, "triage", job_id, str(exc), max_failures=self.settings.agent_max_failures)
                    store.log(session, "warning", "triage_failed", f"Could not match {job.title}: {exc}", job_id=job_id)
                    session.commit()
                    continue
                if match.recommendation == "apply":
                    job.status = JobStatus.shortlisted
                    job.status_reason = match.assessment[:300]
                elif match.recommendation == "maybe":
                    job.status = JobStatus.scored_low
                    job.status_reason = match.assessment[:300]
                else:
                    job.status = JobStatus.filtered_out
                    job.status_reason = (match.concerns[0] if match.concerns else match.assessment)[:300]
                counts[job.status.value] += 1
                session.commit()
        with self._sessions() as session:
            store.log(
                session,
                "info",
                "matched",
                f"Matched new jobs: {counts['shortlisted']} shortlisted, {counts['scored_low']} weak, "
                f"{counts['filtered_out']} filtered out.",
            )
            session.commit()
        self._select()

    def _select(self) -> None:
        with self._sessions() as session:
            if user_settings(session).autonomy_level is AutonomyLevel.observe:
                return
            jobs = session.scalars(
                select(Job).where(Job.status == JobStatus.shortlisted).order_by(Job.first_seen_at, Job.id)
            ).all()
            created = 0
            for job in jobs:
                if created >= self.settings.agent_prepare_per_cycle:
                    break
                if not plausible(session, job):
                    continue
                try:
                    application = open_application(
                        session,
                        job,
                        actor=EventActor.system,
                        reason=f"Selected by the agent: {job.status_reason or 'plausible internship match'}"[:500],
                    )
                except DuplicateApplication:
                    session.rollback()
                    continue
                store.log(
                    session,
                    "info",
                    "application_created",
                    f"Selected {job.title} at {job.company_name}.",
                    job_id=job.id,
                    application_id=application.id,
                )
                session.commit()
                created += 1

    def _isolated(
        self,
        stage: str,
        subject_id: int,
        work: Callable[[Session], None],
        *,
        gave_up: Callable[[Session, str], None] | None = None,
        browser_stage: bool = False,
    ) -> None:
        with self._sessions() as session:
            try:
                work(session)
                return
            except Exception as exc:
                session.rollback()
                error = f"{type(exc).__name__}: {exc}"
        if browser_stage:
            self.close_browser()
        with self._sessions() as session:
            row = store.record_failure(
                session,
                stage,
                subject_id,
                error,
                max_failures=self.settings.agent_max_failures,
                now=self.deps.now(),
            )
            application = session.get(Application, subject_id)
            job_id = application.job_id if application is not None else None
            if row.gave_up:
                if gave_up is not None:
                    gave_up(session, error)
                store.log(
                    session,
                    "error",
                    f"{stage}_gave_up",
                    f"Gave up on this {stage} step after {row.failures} failures. Last error: {error}",
                    job_id=job_id,
                    application_id=subject_id,
                )
            else:
                store.log(
                    session,
                    "warning",
                    f"{stage}_failed",
                    f"The {stage} step failed ({row.failures} of {self.settings.agent_max_failures}); "
                    f"it will be retried later. {error}",
                    job_id=job_id,
                    application_id=subject_id,
                )
            session.commit()


def plausible(session: Session, job: Job) -> bool:
    """Only shortlisted internships on a site the agent can fill, and never a posting already applied to."""

    if job.application is not None or job.closed_at is not None:
        return False
    if job.is_internship is not True or not (job.apply_url or "").strip():
        return False
    if not _can_drive(job):
        return False
    if applied_sibling(session, job) is not None:
        return False
    if job.possible_duplicate_of is not None:
        original = session.get(Job, job.possible_duplicate_of)
        if original is not None and original.application is not None:
            return False
    return True


def store_clear_failures(session: Session, stage: str, subject_id: int) -> None:
    row = store.attempt(session, stage, subject_id)
    if row is not None and not row.gave_up:
        store.clear(session, stage, subject_id)
        session.commit()


def _can_drive(job: Job) -> bool:
    return (job.ats_type or "") in APPLY_ADAPTERS


def _agent_created(session: Session, application: Application) -> bool:
    return (
        session.scalar(
            select(ApplicationEvent.id).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.event_type == "application_created",
                ApplicationEvent.actor == EventActor.system,
            )
        )
        is not None
    )


def _tailored_version(session: Session, application: Application) -> int | None:
    return session.scalar(
        select(ResumeVersion.id)
        .where(ResumeVersion.application_id == application.id, ResumeVersion.kind == ResumeVersionKind.tailored)
        .order_by(ResumeVersion.id.desc())
    )


def _latest_reason(session: Session, application: Application) -> str:
    event = session.scalar(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application.id, ApplicationEvent.event_type == "waiting_for_user")
        .order_by(ApplicationEvent.id.desc())
    )
    return "waiting for you." if event is None else str(event.data.get("reason") or "waiting for you.")
