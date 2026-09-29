import os
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jobhunter.agent import store
from jobhunter.agent.pipeline import AgentDeps, Pipeline, ResumeMaterial, TailorResult, plausible
from jobhunter.agent.worker import AgentAlreadyRunning, AgentWorker
from jobhunter.api.app import create_app
from jobhunter.applications import change_status
from jobhunter.apply.runner import page_context, respond
from jobhunter.apply.decisions import ApplicantData
from jobhunter.config import Settings
from jobhunter.db.migrate import init_database
from jobhunter.db.models import AgentState, Application, Job, UserSettings
from jobhunter.db.records import JobSighting, add_master_resume, add_resume_version, record_job_sighting
from jobhunter.db.session import build_engine, session_factory
from jobhunter.domain.enums import (
    AgentDesired,
    AgentPhase,
    ApplicationStatus,
    AutonomyLevel,
    EventActor,
    JobStatus,
    ResumeVersionKind,
)
from jobhunter.domain.time import utcnow
from jobhunter.ingestion.ports import SourceIdentity
from jobhunter.matching.profiles import ResumeProfile
from tests.apply_fakes import FlowBrowser

RESUME = ResumeProfile(
    skills=["Python", "SQL", "Linux"],
    experience=["Built backend services in Python and SQL for a campus lab."],
    projects=["A compiler written in Python."],
    education=["B.S. Computer Science, expected 2028"],
)


class WorkdayFixtureSource:
    """Looks like a Workday board to the agent. Never touches a network."""

    def __init__(self, postings: list[dict[str, Any]] | None = None, *, fail: bool = False) -> None:
        self.postings = postings if postings is not None else _postings()
        self.fail = fail
        self.calls = 0

    def identify(self) -> SourceIdentity:
        return SourceIdentity(
            key="fixture-workday",
            ats_type="workday",
            board_key="northwind",
            label="Northwind (fixture)",
            company="Northwind Labs",
        )

    def discover(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("the board is down")
        return list(self.postings)

    def fetch_detail(self, job):
        return None


def _postings() -> list[dict[str, Any]]:
    return [
        {
            "external_id": "nw-intern-1",
            "title": "Software Engineer Intern",
            "url": "https://northwind.example/jobs/intern-1",
            "company": "Northwind Labs",
            "locations": ["McLean, VA"],
            "description": "Summer 2027 internship. Build backend services with Python and SQL on Linux.",
            "raw": {"id": "nw-intern-1"},
        },
        {
            "external_id": "nw-intern-2",
            "title": "Data Platform Intern",
            "url": "https://northwind.example/jobs/intern-2",
            "company": "Northwind Labs",
            "locations": ["Remote - United States"],
            "description": "Summer 2027 internship. Python and SQL pipelines.",
            "raw": {"id": "nw-intern-2"},
        },
        {
            "external_id": "nw-senior",
            "title": "Senior Staff Engineer",
            "url": "https://northwind.example/jobs/senior",
            "company": "Northwind Labs",
            "locations": ["McLean, VA"],
            "description": "Ten years of Python experience required.",
            "raw": {"id": "nw-senior"},
        },
    ]


class Clock:
    """Real time plus an offset, so it agrees with timestamps the database sets itself."""

    def __init__(self) -> None:
        self.offset = timedelta()

    def __call__(self):
        return utcnow() + self.offset

    @property
    def now(self):
        return self()

    def advance(self, **delta) -> None:
        self.offset += timedelta(**delta)


class World:
    def __init__(self, settings: Settings, *, source=None, browser=None, tailor_fails: set[str] | None = None):
        init_database(settings)
        _facts_file(settings)
        self.settings = settings
        self.engine = build_engine(settings)
        self.sessions = session_factory(self.engine)
        self.source = source or WorkdayFixtureSource()
        self.browser = browser or FlowBrowser()
        self.browsers_opened = 0
        self.tailor_fails = tailor_fails or set()
        self.tailored: list[int] = []
        self.clock = Clock()
        self.deps = AgentDeps(
            settings=settings,
            sources=lambda: [self.source],
            resume=lambda session: ResumeMaterial(profile=RESUME, text=RESUME.text()),
            tailor=self._tailor,
            browser=self._open_browser,
            now=self.clock,
        )

    def _open_browser(self):
        self.browsers_opened += 1
        return self.browser

    def _tailor(self, session, application: Application) -> TailorResult:
        if any(marker in application.job.title for marker in self.tailor_fails):
            raise RuntimeError(f"pdflatex crashed for {application.job.title}")
        master = add_master_resume(session, path="/tmp/master.tex", sha256=f"master-{application.id}")
        version = add_resume_version(
            session,
            resume=master,
            kind=ResumeVersionKind.tailored,
            application=application,
            sha256=f"tailored-{application.id}",
            tex_path=f"/tmp/applications/{application.id}/resume.tex",
            pdf_path=f"/tmp/applications/{application.id}/resume.pdf",
        )
        self.tailored.append(application.id)
        return TailorResult(version_id=version.id, plan_error=None)

    def pipeline(self, cancel=lambda: False) -> Pipeline:
        return Pipeline(self.deps, self.sessions, report=lambda *args, **kwargs: None, cancel=cancel)

    def worker(self) -> AgentWorker:
        return AgentWorker(self.settings, deps=self.deps, sessions=self.sessions, heartbeat_seconds=0.05)

    def drain(self, pipeline: Pipeline | None = None, limit: int = 60) -> int:
        pipeline = pipeline or self.pipeline()
        for count in range(limit):
            if not pipeline.next_unit():
                return count
        raise AssertionError("the agent kept finding work; it may be looping")

    def applications(self) -> list[Application]:
        with self.sessions() as session:
            rows = session.scalars(select(Application).order_by(Application.id)).all()
            session.expunge_all()
            return list(rows)

    def kinds(self) -> list[str]:
        with self.sessions() as session:
            return [entry.kind for entry in reversed(store.activity(session, 500))]

    def autonomy(self, level: AutonomyLevel) -> None:
        with self.sessions() as session:
            session.get(UserSettings, 1).autonomy_level = level
            session.commit()

    def respond(self, application_id: int, **kwargs) -> None:
        with self.sessions() as session:
            application = session.get(Application, application_id)
            respond(session, application, None, ApplicantData(), **kwargs)
            session.commit()

    def pause_kind(self, application_id: int) -> str | None:
        with self.sessions() as session:
            return page_context(session, session.get(Application, application_id), None)["pause_kind"]

    def close(self) -> None:
        self.engine.dispose()


@pytest.fixture
def agent_settings(tmp_path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8765,
        data_dir=tmp_path,
        log_level="WARNING",
        agent_poll_seconds=0.01,
        agent_apply_interval_seconds=0,
        agent_daily_applications=10,
        agent_max_failures=3,
        agent_max_replays=2,
    )


@pytest.fixture
def world(agent_settings):
    created = World(agent_settings)
    yield created
    created.close()


def _facts_file(settings: Settings) -> None:
    (settings.data_dir / "profile.toml").write_text(
        '[answers]\n"contact.first_name" = "Ada"\n"contact.email" = "ada@example.com"\n',
        encoding="utf-8",
    )


def test_one_cycle_discovers_matches_prepares_applies_and_waits(world) -> None:
    world.worker().run(start=True, idle_exit=True)

    applications = world.applications()
    assert len(applications) == 2
    assert all(item.status is ApplicationStatus.waiting_for_user for item in applications)
    assert sorted(world.tailored) == [item.id for item in applications]
    for item in applications:
        assert world.pause_kind(item.id) == "questions"
    with world.sessions() as session:
        senior = session.scalar(select(Job).where(Job.title == "Senior Staff Engineer"))
        assert senior.application is None
        assert senior.status is not JobStatus.shortlisted
        row = store.state(session)
        assert row.pid is None
        assert row.phase is AgentPhase.stopped
        assert row.next_discovery_at is not None
    kinds = world.kinds()
    for kind in ("worker_started", "source_checked", "matched", "application_created", "prepared", "apply_started", "waiting_for_user", "worker_stopped"):
        assert kind in kinds
    assert ("fill", "#email", "ada@example.com") in world.browser.calls
    assert world.browser.submits == []


def test_after_answers_and_confirmation_the_agent_submits_and_never_applies_twice(world) -> None:
    world.worker().run(start=True, idle_exit=True)
    first, second = world.applications()

    world.respond(first.id, action="edit", field_id="workAuthorization", value="Yes")
    world.worker().run(idle_exit=True)
    assert world.pause_kind(first.id) == "confirm_submit"
    assert world.browser.submits == []

    world.respond(first.id, action="confirm_submit")
    world.worker().run(idle_exit=True)
    submitted = next(item for item in world.applications() if item.id == first.id)
    assert submitted.status is ApplicationStatus.submitted
    assert len(world.browser.submits) == 1
    assert "submitted" in world.kinds()
    assert next(item for item in world.applications() if item.id == second.id).status is ApplicationStatus.waiting_for_user

    with world.sessions() as session:
        store.state(session).next_discovery_at = world.clock.now
        session.commit()
    world.worker().run(idle_exit=True)
    assert len(world.applications()) == 2
    assert len(world.browser.submits) == 1


def test_a_posting_already_applied_under_another_listing_is_not_selected(world) -> None:
    world.drain()
    with world.sessions() as session:
        first = session.scalars(select(Application).order_by(Application.id)).first()
        first.status = ApplicationStatus.submitted
        first.submitted_at = utcnow()
        twin = record_job_sighting(
            session,
            JobSighting(
                title=first.job.title,
                company=first.job.company_name,
                url=first.job.apply_url + "?utm_source=linkedin",
                ats_type="workday",
                external_id="twin",
            ),
        )
        twin.is_internship = True
        twin.status = JobStatus.shortlisted
        twin.canonical_apply_url = first.job.canonical_apply_url
        session.flush()
        assert plausible(session, twin) is False
        session.rollback()


def test_one_broken_application_does_not_stop_the_others_and_is_retried_a_bounded_number_of_times(world) -> None:
    class Flaky(FlowBrowser):
        def navigate(self, url, expect=None, timeout_ms=None):
            if "intern-1" in url:
                raise RuntimeError("renderer crashed")
            super().navigate(url, expect, timeout_ms)

    world.browser = Flaky()
    world.drain()
    by_url = {item.job_id: item for item in world.applications()}
    with world.sessions() as session:
        broken = session.scalar(select(Application).join(Job).where(Job.apply_url.contains("intern-1")))
        healthy = session.scalar(select(Application).join(Job).where(Job.apply_url.contains("intern-2")))
        assert healthy.status is ApplicationStatus.waiting_for_user
        assert broken.status is ApplicationStatus.ready_to_apply
        attempt = store.attempt(session, "apply", broken.id)
        assert attempt.failures == 1
        assert attempt.gave_up is False
        assert store.aware(attempt.next_attempt_at) > world.clock.now
        broken_id = broken.id
    assert by_url
    assert "apply_failed" in world.kinds()

    for _ in range(5):
        world.clock.advance(hours=3)
        world.drain()
    with world.sessions() as session:
        attempt = store.attempt(session, "apply", broken_id)
        assert attempt.failures == world.settings.agent_max_failures
        assert attempt.gave_up is True
    assert "apply_gave_up" in world.kinds()
    assert world.drain() == 0


def test_a_tailoring_failure_is_isolated(world) -> None:
    world.tailor_fails = {"Data Platform"}
    world.drain()
    statuses = {item.job_id: item.status for item in world.applications()}
    assert ApplicationStatus.waiting_for_user in statuses.values()
    assert ApplicationStatus.tailoring in statuses.values()
    assert "prepare_failed" in world.kinds()


def test_discovery_failure_is_logged_and_the_agent_keeps_running(agent_settings) -> None:
    world = World(agent_settings, source=WorkdayFixtureSource(fail=True))
    try:
        world.drain()
        assert "source_failed" in world.kinds()
        assert world.applications() == []
    finally:
        world.close()


def test_rate_limits_hold_back_new_applications(agent_settings) -> None:
    limited = agent_settings.model_copy(update={"agent_daily_applications": 1})
    world = World(limited)
    try:
        world.drain()
        statuses = sorted(item.status.value for item in world.applications())
        assert statuses == ["ready_to_apply", "waiting_for_user"]
        assert world.kinds().count("apply_started") == 1
    finally:
        world.close()

    spaced = agent_settings.model_copy(update={"agent_apply_interval_seconds": 3600})
    world = World(spaced)
    try:
        world.drain()
        assert world.kinds().count("apply_started") == 1
        world.clock.advance(hours=2)
        world.drain()
        assert world.kinds().count("apply_started") == 2
    finally:
        world.close()


def test_observe_mode_discovers_and_scores_but_never_applies(world) -> None:
    world.autonomy(AutonomyLevel.observe)
    world.drain()
    assert world.applications() == []
    with world.sessions() as session:
        assert session.scalar(select(Job).where(Job.status == JobStatus.shortlisted)) is not None
    assert world.browsers_opened == 0


def test_an_application_that_keeps_getting_interrupted_stops_for_the_user(world) -> None:
    flag = {"on": False}

    class Interrupting(FlowBrowser):
        def navigate(self, url, expect=None, timeout_ms=None):
            super().navigate(url, expect, timeout_ms)
            flag["on"] = True

    world.browser = Interrupting()
    world.source.postings = world.source.postings[:1]
    pipeline = world.pipeline(cancel=lambda: flag["on"])
    for _ in range(world.settings.agent_max_replays + 6):
        flag["on"] = False
        pipeline.next_unit()
    (application,) = world.applications()
    assert world.kinds().count("apply_started") == 1
    assert application.status is ApplicationStatus.waiting_for_user
    assert "replay_limit" in world.kinds()
    assert application.submit_intent_at is None


def test_startup_recovery_never_resubmits(world) -> None:
    world.source.postings = world.source.postings[:1]
    world.drain()
    (application,) = world.applications()
    world.respond(application.id, action="edit", field_id="workAuthorization", value="Yes")
    world.drain()
    world.respond(application.id, action="confirm_submit")
    with world.sessions() as session:
        row = session.get(Application, application.id)
        respond_state = page_context(session, row, None)
        assert respond_state["resume_queued"] is True
        change_status(session, row, to=ApplicationStatus.applying, actor=EventActor.system, reason="Crash test.")
        row.submit_intent_at = utcnow()
        session.commit()
    world.worker().run(start=True, idle_exit=True)
    assert world.pause_kind(application.id) == "verify_submit"
    assert world.browser.submits == []
    assert "submit_unverified" in world.kinds()


def test_stop_request_parks_the_worker_and_closes_the_browser(world) -> None:
    world.source.postings = world.source.postings[:1]
    world.worker().run(start=True, idle_exit=True)
    assert world.browser.closed is True
    with world.sessions() as session:
        store.set_desired(session, AgentDesired.stopped)
        session.commit()
    world.browser.calls.clear()
    world.worker().run(idle_exit=True)
    assert world.browser.calls == []
    with world.sessions() as session:
        assert store.state(session).phase is AgentPhase.stopped


def test_only_one_worker_runs_at_a_time(world) -> None:
    with world.sessions() as session:
        row = store.state(session)
        row.pid = os.getppid()
        row.heartbeat_at = utcnow()
        session.commit()
    with pytest.raises(AgentAlreadyRunning):
        world.worker().run(idle_exit=True)

    with world.sessions() as session:
        row = store.state(session)
        row.heartbeat_at = utcnow() - timedelta(minutes=5)
        session.commit()
    world.worker().run(idle_exit=True)


def test_agent_api_start_stop_settings_activity_and_retry(agent_settings) -> None:
    app = create_app(agent_settings)
    client = TestClient(app)

    status = client.get("/api/agent").json()
    assert status["desired"] == "stopped"
    assert status["alive"] is False
    assert status["phase"] == "stopped"
    assert "not running" in status["message"]
    assert status["autonomy_level"] == "assist"

    assert client.post("/api/agent/start").json()["desired"] == "running"
    assert client.post("/api/agent/discover").status_code == 200
    changed = client.put("/api/agent/settings", json={"autonomy_level": "supervised", "discovery_interval_hours": 6})
    assert changed.json()["autonomy_level"] == "supervised"
    assert changed.json()["discovery_interval_hours"] == 6
    assert client.put("/api/agent/settings", json={"autonomy_level": "reckless"}).status_code == 422
    assert client.post("/api/agent/stop").json()["desired"] == "stopped"

    kinds = [entry["kind"] for entry in client.get("/api/agent/activity").json()]
    assert kinds[:4] == ["agent_stop_requested", "settings_changed", "discovery_requested", "agent_start_requested"]

    world = World(agent_settings)
    try:
        class Broken(FlowBrowser):
            def navigate(self, url, expect=None, timeout_ms=None):
                raise RuntimeError("renderer crashed")

        world.browser = Broken()
        world.autonomy(AutonomyLevel.assist)
        world.source.postings = world.source.postings[:1]
        world.drain()
        (application,) = world.applications()
    finally:
        world.close()
    assert client.get("/api/agent").json()["failing"] == 1
    assert client.post(f"/api/agent/applications/{application.id}/retry").status_code == 200
    assert client.post(f"/api/agent/applications/{application.id}/retry").status_code == 409
    assert client.get("/api/agent").json()["failing"] == 0


def test_review_hands_the_resume_to_a_running_worker(agent_settings) -> None:
    world = World(agent_settings)
    try:
        world.source.postings = world.source.postings[:1]
        world.drain()
        (application,) = world.applications()
    finally:
        world.close()

    app = create_app(agent_settings)
    opened: list[int] = []
    app.state.browser_factory = lambda: opened.append(1) or FlowBrowser()
    with app.state.session_factory() as session:
        row = session.get(AgentState, 1)
        row.pid = os.getpid()
        row.heartbeat_at = utcnow()
        session.commit()
    client = TestClient(app)
    answered = client.post(
        f"/api/applications/{application.id}/review",
        json={"action": "edit", "field_id": "workAuthorization", "value": "Yes"},
    )
    assert answered.status_code == 200
    body = answered.json()
    assert body["resume_queued"] is True
    assert body["agent_owns_browser"] is True
    assert opened == []
    assert client.post(f"/api/applications/{application.id}/run").status_code == 409
