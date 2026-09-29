"""The agent worker process. It owns the one visible browser and talks to the API only through SQLite."""

import logging
import os
import signal
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

from jobhunter.agent import store
from jobhunter.agent.config import load_sources
from jobhunter.agent.pipeline import AgentDeps, Pipeline, ResumeMaterial, TailorResult
from jobhunter.browser.manager import BrowserManager
from jobhunter.config import Settings
from jobhunter.db.migrate import init_database
from jobhunter.db.models import Application
from jobhunter.db.session import build_engine, session_factory
from jobhunter.domain.enums import AgentDesired, AgentPhase
from jobhunter.domain.time import utcnow
from jobhunter.llm.client import client_from_environment
from jobhunter.llm.errors import LLMError
from jobhunter.matching.profiles import resume_profile_from_parsed
from jobhunter.resume.master import MasterResumeError, validate_master
from jobhunter.resume.tailoring import LLMTailoringPlanner, tailor_application_resume

logger = logging.getLogger(__name__)


class AgentAlreadyRunning(Exception):
    """Another worker process holds the agent."""


class _NoPlanner:
    """Used when no LLM is configured. Tailoring then keeps an unchanged, compiled copy of the master."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def plan(self, *_args, **_kwargs):
        raise LLMError(self._reason)


def default_deps(settings: Settings) -> AgentDeps:
    def resume(session: Session) -> ResumeMaterial | None:
        try:
            report = validate_master(session, settings)
        except MasterResumeError:
            return None
        profile = resume_profile_from_parsed(report.parsed)
        return ResumeMaterial(profile=profile, text=profile.text())

    def tailor(session: Session, application: Application) -> TailorResult:
        try:
            planner = LLMTailoringPlanner(client_from_environment())
        except LLMError as exc:
            planner = _NoPlanner(f"no LLM is configured: {exc}")
        report = tailor_application_resume(session, settings, application.id, planner)
        return TailorResult(version_id=report.version_id, plan_error=report.plan_error)

    def browser() -> BrowserManager:
        manager = BrowserManager(profile_dir=settings.data_dir / "browser-profile")
        manager.open()
        return manager

    return AgentDeps(settings=settings, sources=lambda: load_sources(settings), resume=resume, tailor=tailor, browser=browser)


class AgentWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        deps: AgentDeps | None = None,
        sessions: Callable[[], Session] | None = None,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        self.settings = settings
        if sessions is None:
            init_database(settings)
            self._engine = build_engine(settings)
            sessions = session_factory(self._engine)
        else:
            self._engine = None
        self._sessions = sessions
        self._shutdown = threading.Event()
        self._heartbeat_seconds = heartbeat_seconds
        self.pid = os.getpid()
        self.pipeline = Pipeline(
            deps or default_deps(settings),
            sessions,
            report=self._report,
            cancel=self._should_pause,
        )

    def request_shutdown(self, *_args: object) -> None:
        self._shutdown.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

    def run(self, *, start: bool = False, max_units: int | None = None, idle_exit: bool = False) -> None:
        """Serve until shutdown. `max_units` and `idle_exit` exist so tests can run a bounded loop."""

        self._claim(start)
        beat = threading.Thread(target=self._heartbeat, name="agent-heartbeat", daemon=True)
        beat.start()
        units = 0
        recovered = False
        try:
            while not self._shutdown.is_set():
                if self._desired() is not AgentDesired.running:
                    if self.pipeline.browser_open:
                        self.pipeline.close_browser()
                    recovered = False
                    self._report(AgentPhase.stopped, "Stopped. Start the agent to continue.")
                    if idle_exit:
                        return
                    self._shutdown.wait(self.settings.agent_poll_seconds)
                    continue
                if not recovered:
                    self.pipeline.recover()
                    recovered = True
                did = self.pipeline.next_unit()
                units += did
                if max_units is not None and units >= max_units:
                    return
                if not did:
                    waiting = self.pipeline.waiting_count()
                    if waiting:
                        self._report(AgentPhase.waiting_for_user, f"{waiting} application(s) are waiting for you.")
                    else:
                        self._report(AgentPhase.idle, "Nothing to do until the next discovery run.")
                    if idle_exit:
                        return
                    self._shutdown.wait(self.settings.agent_poll_seconds)
        finally:
            self._report(AgentPhase.stopping, "Stopping.")
            self.pipeline.close_browser()
            self._shutdown.set()
            beat.join(timeout=self._heartbeat_seconds + 1)
            self._release()
            if self._engine is not None:
                self._engine.dispose()

    def _claim(self, start: bool) -> None:
        with self._sessions() as session:
            row = store.state(session)
            if row.pid not in (None, self.pid) and store.worker_alive(row) and _process_exists(row.pid):
                raise AgentAlreadyRunning(f"another agent worker is running (pid {row.pid})")
            row.pid = self.pid
            row.started_at = utcnow()
            row.heartbeat_at = utcnow()
            row.phase = AgentPhase.starting
            row.current_job_id = None
            row.current_application_id = None
            row.current_step = "Starting."
            if start:
                store.set_desired(session, AgentDesired.running)
            store.log(session, "info", "worker_started", f"Agent worker started (pid {self.pid}).")
            session.commit()

    def _release(self) -> None:
        with self._sessions() as session:
            row = store.state(session)
            if row.pid == self.pid:
                row.pid = None
                row.phase = AgentPhase.stopped
                row.current_job_id = None
                row.current_application_id = None
                row.current_step = None
                row.message = "The worker process is not running."
                row.updated_at = utcnow()
            store.log(session, "info", "worker_stopped", f"Agent worker stopped (pid {self.pid}).")
            session.commit()

    def _heartbeat(self) -> None:
        while not self._shutdown.wait(self._heartbeat_seconds):
            try:
                with self._sessions() as session:
                    row = store.state(session)
                    if row.pid == self.pid:
                        row.heartbeat_at = utcnow()
                        session.commit()
            except Exception:
                logger.exception("agent heartbeat failed")

    def _desired(self) -> AgentDesired:
        with self._sessions() as session:
            return store.state(session).desired

    def _should_pause(self) -> bool:
        return self._shutdown.is_set() or self._desired() is not AgentDesired.running

    def _report(
        self,
        phase: AgentPhase,
        step: str,
        *,
        job_id: int | None = None,
        application_id: int | None = None,
    ) -> None:
        with self._sessions() as session:
            row = store.state(session)
            row.phase = phase
            row.current_step = step[:300]
            row.current_job_id = job_id
            row.current_application_id = application_id
            row.heartbeat_at = utcnow()
            row.updated_at = utcnow()
            session.commit()


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_worker(settings: Settings, *, start: bool = False) -> None:
    worker = AgentWorker(settings)
    worker.install_signal_handlers()
    try:
        worker.run(start=start)
    except AgentAlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc
