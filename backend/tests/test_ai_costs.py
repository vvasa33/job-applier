import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jobhunter.ai import AIService, BudgetExceeded, CachedSemanticMatcher, CachedTailoringPlanner
from jobhunter.ai import ledger
from jobhunter.ai.hashing import job_content_hash
from jobhunter.api.app import create_app
from jobhunter.applications import change_status, open_application
from jobhunter.db.models import AICache, AIUsage, Application, Job, UserSettings
from jobhunter.db.records import JobSighting, add_master_resume, add_resume_version, record_job_sighting
from jobhunter.domain.enums import (
    ApplicationStatus,
    CsRelevance,
    EventActor,
    LocationClass,
    ResumeVersionKind,
)
from jobhunter.llm.client import LLMClient
from jobhunter.llm.config import approximate_cost_usd, token_prices
from jobhunter.llm.provider import FakeProvider
from jobhunter.matching.profiles import JobProfile, ResumeProfile
from tests.test_agent import World, agent_settings, world  # noqa: F401

JOB = JobProfile(
    title="Software Engineering Intern",
    company="Northwind",
    locations=["McLean, VA"],
    location_class=LocationClass.dmv,
    is_internship=True,
    cs_relevance=CsRelevance.relevant,
    description_text="Build services in Python.",
)
RESUME = ResumeProfile(skills=["Python"], experience=["Built a Python service."])
SEMANTIC = json.dumps({"explanation": "Python overlaps.", "matched_skills": ["Python"]})
PLAN = json.dumps(
    {"explanation": "Keep the Python bullet.", "bullets": [{"text": "Built a Python service.", "source_ids": ["exp-1"]}]}
)


@pytest.fixture(autouse=True)
def _no_local_env(monkeypatch):
    monkeypatch.setattr("jobhunter.llm.config.load_local_env", lambda: None)
    for name in ("JOBHUNTER_LLM_MODEL", "JOBHUNTER_LLM_MODEL_CHEAP", "JOBHUNTER_LLM_MODEL_STRONG"):
        monkeypatch.delenv(name, raising=False)


def _service(world: World, contents: list[str]) -> tuple[AIService, FakeProvider]:
    provider = FakeProvider(contents)
    client = LLMClient(provider, max_attempts=1, sleep=lambda _s: None)
    return AIService(world.sessions, client=lambda: client), provider


def _budget(world: World, amount: str) -> None:
    with world.sessions() as session:
        session.get(UserSettings, 1).daily_llm_budget_usd = Decimal(amount)
        session.commit()


def _usage(world: World) -> list[AIUsage]:
    with world.sessions() as session:
        return list(session.scalars(select(AIUsage).order_by(AIUsage.id)).all())


def _job(world: World, title: str = "Software Engineer Intern") -> int:
    with world.sessions() as session:
        job = record_job_sighting(
            session,
            JobSighting(title=title, company="Northwind Labs", url=f"https://northwind.example/{title}", ats_type="workday", external_id=title),
        )
        session.commit()
        return job.id


def test_cheap_tier_classifies_and_strong_tier_tailors_unless_a_model_is_pinned(monkeypatch) -> None:
    provider = FakeProvider([SEMANTIC, PLAN, SEMANTIC])
    client = LLMClient(provider, max_attempts=1)
    client.semantic_match(JOB, RESUME)
    client.tailor_resume(JOB, [("exp-1", "Built a Python service.")])
    monkeypatch.setenv("JOBHUNTER_LLM_MODEL_CHEAP", "gpt-4o-mini")
    client.semantic_match(JOB, RESUME)
    assert [item.model for item in provider.requests] == ["gpt-4.1-nano", "gpt-4.1", "gpt-4o-mini"]

    monkeypatch.setenv("JOBHUNTER_LLM_MODEL", "gpt-4o")
    assert client.model_for("resume_tailoring") == "gpt-4o"
    assert client.model_for("difficult_question") == "gpt-4o"
    assert client.model_for("question_suggestion") == "gpt-4o-mini"
    pinned = LLMClient(provider, model="test-model")
    assert {pinned.model_for(item) for item in ("semantic_matching", "resume_tailoring")} == {"test-model"}


def test_each_model_is_priced_on_its_own_rates() -> None:
    assert approximate_cost_usd(1_000_000, 0, "gpt-4.1-nano") == Decimal("0.100000")
    assert approximate_cost_usd(0, 1_000_000, "gpt-4.1") == Decimal("8.000000")
    assert approximate_cost_usd(1_000_000, 0, "gpt-4.1-2025-04-14") == Decimal("2.000000")
    assert token_prices("some-new-model") == token_prices("gpt-4.1-mini")


def test_content_hash_ignores_markup_and_spacing_but_not_wording() -> None:
    same = JOB.model_copy(update={"description_text": "<p>Build   services\nin <b>Python</b>.</p>"})
    reworded = JOB.model_copy(update={"description_text": "Build services in Go."})
    reclassified = JOB.model_copy(update={"location_class": LocationClass.us_remote})
    assert job_content_hash(same) == job_content_hash(JOB)
    assert job_content_hash(reclassified) == job_content_hash(JOB)
    assert job_content_hash(reworded) != job_content_hash(JOB)


def test_an_unchanged_job_is_analyzed_once_and_later_hits_are_free(world) -> None:
    ai, provider = _service(world, [SEMANTIC, SEMANTIC])
    job_id = _job(world)
    matcher = CachedSemanticMatcher(ai, job_id=job_id)

    first = matcher.analyze(JOB, RESUME)
    again = matcher.analyze(JOB.model_copy(update={"description_text": "<p>Build services in Python.</p>"}), RESUME)
    assert again == first
    assert len(provider.requests) == 1

    matcher.analyze(JOB.model_copy(update={"description_text": "Build services in Rust."}), RESUME)
    assert len(provider.requests) == 2

    rows = _usage(world)
    assert [row.status for row in rows] == ["ok", "cached", "ok"]
    assert rows[1].cost_usd == 0 and rows[1].saved_usd == rows[0].cost_usd
    assert rows[0].tier == "cheap" and rows[0].model == "gpt-4.1-nano"
    assert all(row.job_id == job_id for row in rows)
    with world.sessions() as session:
        assert ledger.total_for(session, job_id=job_id) == rows[0].cost_usd + rows[2].cost_usd
        assert ledger.spent_on(session, ledger.local_day()) == rows[0].cost_usd + rows[2].cost_usd
        assert session.scalar(select(AICache.hits).where(AICache.cache_key == rows[0].cache_key)) == 1


def test_over_budget_nonessential_work_is_refused_and_essential_work_is_not(world) -> None:
    _budget(world, "0")
    ai, provider = _service(world, [PLAN])
    matcher = CachedSemanticMatcher(ai)
    with pytest.raises(BudgetExceeded):
        matcher.analyze(JOB, RESUME)
    assert provider.requests == []
    assert ai.can_spend("semantic_matching") is False

    planner = CachedTailoringPlanner(ai, job_id=None, application_id=None, essential=True)
    planner.plan(JOB, [("exp-1", "Built a Python service.")], [], [])
    assert len(provider.requests) == 1
    assert [(row.status, row.essential) for row in _usage(world)] == [("refused", False), ("ok", True)]
    assert ai.budget().exceeded is True


def test_a_tailoring_retry_reuses_the_paid_plan_and_costs_are_attributed_to_the_version(world) -> None:
    ai, provider = _service(world, [PLAN])
    job_id = _job(world)
    with world.sessions() as session:
        application = open_application(session, session.get(Job, job_id), actor=EventActor.system)
        session.commit()
        application_id = application.id
    planner = CachedTailoringPlanner(ai, job_id=job_id, application_id=application_id, essential=False)
    entries = [("exp-1", "Built a Python service.")]

    planner.plan(JOB, entries, ["skill: Python"], ["Python"])
    planner.plan(JOB, entries, ["skill: Python"], ["Python"])
    assert len(provider.requests) == 1

    with world.sessions() as session:
        master = add_master_resume(session, path="/tmp/master.tex", sha256="master")
        version = add_resume_version(
            session,
            resume=master,
            kind=ResumeVersionKind.tailored,
            application=session.get(Application, application_id),
            sha256="tailored",
            tex_path="/tmp/resume.tex",
            pdf_path="/tmp/resume.pdf",
        )
        session.commit()
        version_id = version.id
    ai.attach_version(application_id, version_id)

    paid = _usage(world)[0].cost_usd
    assert paid > 0
    with world.sessions() as session:
        assert ledger.total_for(session, application_id=application_id) == paid
        assert ledger.total_for(session, resume_version_id=version_id) == paid
        assert ledger.total_for(session, job_id=job_id) == paid
        (today,) = ledger.daily(session)
        assert (today.calls, today.cached, today.saved) == (1, 1, paid)


def test_the_agent_uses_ai_only_after_deterministic_filters(world) -> None:
    ai, provider = _service(world, [SEMANTIC, SEMANTIC])
    world.deps.ai = ai
    world.drain()
    # Two interns pass the hard filters; the senior role is rejected without a model call.
    assert len(provider.requests) == 2
    assert {item.model for item in provider.requests} == {"gpt-4.1-nano"}
    with world.sessions() as session:
        senior = session.scalar(select(Job).where(Job.title == "Senior Staff Engineer"))
        assert ledger.total_for(session, job_id=senior.id) == 0
    assert len(world.tailored) == 2


def test_over_budget_the_agent_stops_new_ai_work_but_finishes_running_applications(world) -> None:
    ai, provider = _service(world, [])
    world.deps.ai = ai
    _budget(world, "0")

    world.drain()
    assert provider.requests == []
    assert world.tailored == []
    applications = world.applications()
    assert len(applications) == 2
    assert all(item.status in (ApplicationStatus.found, ApplicationStatus.matched) for item in applications)
    assert world.kinds().count("ai_budget_reached") == 1
    assert [row.status for row in _usage(world)] == ["refused"]

    ready = applications[0]
    with world.sessions() as session:
        application = session.get(Application, ready.id)
        master = add_master_resume(session, path="/tmp/master.tex", sha256="master")
        version = add_resume_version(
            session,
            resume=master,
            kind=ResumeVersionKind.tailored,
            application=application,
            sha256="tailored",
            tex_path="/tmp/resume.tex",
            pdf_path="/tmp/resume.pdf",
        )
        for target in (ApplicationStatus.matched, ApplicationStatus.saved, ApplicationStatus.tailoring):
            if application.status is not target:
                change_status(session, application, to=target, actor=EventActor.system, reason="test")
        change_status(
            session,
            application,
            to=ApplicationStatus.ready_to_apply,
            actor=EventActor.system,
            reason="test",
            resume_version_id=version.id,
        )
        session.commit()

    world.drain()
    by_id = {item.id: item for item in world.applications()}
    assert by_id[ready.id].status is ApplicationStatus.waiting_for_user
    assert by_id[applications[1].id].status in (ApplicationStatus.found, ApplicationStatus.matched)
    assert world.kinds().count("ai_budget_reached") == 1

    _budget(world, "5")
    world.drain()
    assert len(world.tailored) == 1


def test_the_api_reports_costs_and_accepts_a_budget(agent_settings, monkeypatch) -> None:
    monkeypatch.delenv("JOBHUNTER_LLM_API_KEY", raising=False)
    world = World(agent_settings)
    try:
        ai, _provider = _service(world, [SEMANTIC])
        job_id = _job(world)
        CachedSemanticMatcher(ai, job_id=job_id).analyze(JOB, RESUME)
        paid = float(_usage(world)[0].cost_usd)
    finally:
        world.close()

    client = TestClient(create_app(agent_settings))
    status = client.get("/api/agent").json()["ai"]
    assert status["configured"] is False
    assert status["cheap_model"] == "gpt-4.1-nano" and status["strong_model"] == "gpt-4.1"
    assert status["spent_today_usd"] == pytest.approx(paid)
    assert status["budget_usd"] == 2.0 and status["exceeded"] is False
    assert status["by_purpose"] == {"semantic_matching": pytest.approx(paid)}
    assert status["days"][0]["calls"] == 1

    changed = client.put("/api/agent/settings", json={"daily_llm_budget_usd": 0})
    assert changed.json()["ai"]["exceeded"] is True
    assert client.put("/api/agent/settings", json={"daily_llm_budget_usd": -1}).status_code == 422
    assert client.get(f"/api/jobs/{job_id}").json()["ai_cost_usd"] == pytest.approx(paid)
