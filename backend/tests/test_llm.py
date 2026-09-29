import json
import logging

import httpx
import pytest

from jobhunter.config import Settings
from jobhunter.domain.enums import CsRelevance, LocationClass
from jobhunter.llm.client import LLMClient
from jobhunter.llm.config import api_key_from_environment, approximate_cost_usd
from jobhunter.local_env import load_local_env
from jobhunter.llm.errors import LLMConfigError, LLMError, ProviderError
from jobhunter.llm.http import HttpLLMProvider
from jobhunter.llm.provider import FakeProvider
from jobhunter.llm.semantic import LLMSemanticMatcher
from jobhunter.matching.profiles import JobProfile, ResumeProfile
from jobhunter.matching.service import match_job

SECRET = "sk-test-secret-do-not-log"
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


def _client(provider: FakeProvider, **kwargs) -> LLMClient:
    delays: list[float] = []
    return LLMClient(
        provider,
        model="test-model",
        timeout_s=30,
        max_attempts=3,
        sleep=delays.append,
        **kwargs,
    )


def test_four_methods_parse_fake_structured_output() -> None:
    provider = FakeProvider(
        [
            json.dumps({"summary": "Backend internship.", "requirements": ["Python"], "evidence_quotes": ["Python"]}),
            json.dumps({"explanation": "Python overlaps.", "matched_skills": ["Python"]}),
            json.dumps(
                {
                    "explanation": "Keep the Python bullet.",
                    "bullets": [{"text": "Built a Python service.", "source_ids": ["exp-1"]}],
                }
            ),
            json.dumps(
                {
                    "field_label": "Work authorization",
                    "suggested_value": "Yes",
                    "confidence": "low",
                    "needs_human": True,
                    "reason": "This needs confirmation.",
                }
            ),
        ]
    )
    client = _client(provider)

    analysis = client.analyze_job(JOB)
    semantic = client.semantic_match(JOB, RESUME)
    plan = client.tailor_resume(JOB, [("exp-1", "Built a Python service.")])
    answer = client.suggest_answer(
        field_label="Work authorization",
        field_type="select",
        job_title=JOB.title,
        context="Authorized to work in the US.",
    )

    assert analysis.output.summary == "Backend internship."
    assert semantic.output.matched_skills == ["Python"]
    assert plan.output.bullets[0].source_ids == ["exp-1"]
    assert answer.output.needs_human is True
    assert [request.purpose for request in provider.requests] == [
        "job_analysis",
        "semantic_matching",
        "resume_tailoring",
        "question_suggestion",
    ]
    assert all("tools" not in request.model_dump() for request in provider.requests)
    assert provider.timeouts == [30, 30, 30, 30]
    assert semantic.usage.cost_usd == "0.000112"
    assert semantic.usage.input_tokens == 120
    assert len(client.usage) == 4


def test_retries_transient_failures_then_records_attempts(caplog) -> None:
    caplog.set_level(logging.INFO, logger="jobhunter")
    provider = FakeProvider(
        [json.dumps({"explanation": "Overlap on Python."})],
        failures=[
            ProviderError("provider request timed out", retryable=True),
            ProviderError("provider request failed with HTTP 503", retryable=True),
        ],
    )
    client = _client(provider)
    result = client.semantic_match(JOB, RESUME)

    assert result.usage.attempts == 3
    assert len(provider.requests) == 3
    assert SECRET not in caplog.text
    assert "purpose=semantic_matching" in caplog.text
    assert "input_tokens=120" in caplog.text


def test_auth_failure_is_not_retried() -> None:
    provider = FakeProvider(
        [],
        failures=[ProviderError("provider request failed with HTTP 401", retryable=False)],
    )
    client = _client(provider)
    with pytest.raises(LLMError, match="semantic_matching failed after 1 attempts"):
        client.semantic_match(JOB, RESUME)
    assert len(provider.requests) == 1
    assert client.usage == []


def test_invalid_json_is_not_retried_and_body_is_not_required_in_the_error() -> None:
    provider = FakeProvider(["this is not json"])
    client = _client(provider)
    with pytest.raises(LLMError, match="did not match SemanticAnalysis") as caught:
        client.semantic_match(JOB, RESUME)
    assert "this is not json" not in str(caught.value)
    assert len(provider.requests) == 1
    assert client.usage[0].status == "invalid"


def test_retry_limit_is_enforced() -> None:
    provider = FakeProvider(
        [],
        failures=[ProviderError("provider request timed out", retryable=True) for _ in range(5)],
    )
    client = _client(provider)
    with pytest.raises(LLMError, match="failed after 3 attempts"):
        client.analyze_job(JOB)
    assert len(provider.requests) == 3


def test_missing_token_counts_leave_cost_unset() -> None:
    from jobhunter.llm.provider import TokenlessProvider

    provider = TokenlessProvider([json.dumps({"explanation": "No token counts."})])
    result = _client(provider).semantic_match(JOB, RESUME)
    assert result.usage.cost_usd is None
    assert approximate_cost_usd(None, None) is None


def test_api_key_comes_from_the_environment_or_dotenv(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'llm_api_key = "{SECRET}"\n', encoding="utf-8")
    monkeypatch.setenv("JOBHUNTER_CONFIG", str(config))
    monkeypatch.delenv("JOBHUNTER_LLM_API_KEY", raising=False)
    monkeypatch.setattr("jobhunter.llm.config.load_local_env", lambda: None)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert SECRET not in str(settings.model_dump())
    with pytest.raises(LLMConfigError, match="JOBHUNTER_LLM_API_KEY"):
        api_key_from_environment()

    env_file = tmp_path / ".env"
    env_file.write_text(f'JOBHUNTER_LLM_API_KEY="{SECRET}"\n', encoding="utf-8")
    monkeypatch.setattr("jobhunter.local_env.env_files", lambda: [env_file])
    monkeypatch.setattr("jobhunter.llm.config.load_local_env", load_local_env)
    assert api_key_from_environment() == SECRET

    monkeypatch.setenv("JOBHUNTER_LLM_API_KEY", "sk-from-the-shell")
    assert api_key_from_environment() == "sk-from-the-shell"


def test_http_provider_sends_the_key_without_logging_it(caplog) -> None:
    caplog.set_level(logging.INFO, logger="jobhunter")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"explanation": "ok"})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    provider = HttpLLMProvider(
        api_key=SECRET,
        base_url="https://llm.example/v1",
        transport=httpx.MockTransport(handler),
    )
    client = LLMClient(provider, model="test-model", timeout_s=5, max_attempts=1, sleep=lambda _seconds: None)
    result = client.semantic_match(JOB, RESUME)

    assert seen["authorization"] == f"Bearer {SECRET}"
    assert "tools" not in seen["body"]
    assert SECRET not in caplog.text
    assert SECRET not in repr(provider)
    assert result.usage.input_tokens == 10
    assert result.usage.cost_usd == "0.000007"


def test_semantic_matcher_uses_the_existing_matching_interface() -> None:
    provider = FakeProvider(
        [json.dumps({"explanation": "The Python work is relevant.", "matched_skills": ["Python"]})]
    )
    result = match_job(JOB, RESUME, semantic=LLMSemanticMatcher(_client(provider)))
    assert result.semantic is not None
    assert result.semantic.explanation == "The Python work is relevant."
    assert result.recommendation == "apply"


def test_malformed_tailoring_plan_is_rejected() -> None:
    provider = FakeProvider([json.dumps({"bullets": [{"source_ids": ["exp-1"]}]})])
    with pytest.raises(LLMError, match="did not match TailoringPlan"):
        _client(provider).tailor_resume(JOB, [("exp-1", "Built a service.")])


def test_tailoring_prompt_carries_the_no_invention_rules() -> None:
    provider = FakeProvider([json.dumps({"bullets": []})])
    _client(provider).tailor_resume(
        JOB,
        [("b1", "[Experience / Northwind] Built a service.")],
        requirements=["skill: Python"],
        evidence=["Relevant resume entry: Built a service."],
    )
    request = provider.requests[0]
    for rule in (
        "Never invent experience.",
        "Never invent metrics.",
        "Never invent employers.",
        "Never invent dates.",
        "Never invent skills.",
        "Never invent education.",
        "Never claim experience that cannot be traced to the master resume.",
        "You may rewrite, reorder, remove, combine, and emphasize truthful information.",
        "source_ids",
    ):
        assert rule in request.system
    assert "b1: [Experience / Northwind] Built a service." in request.user
    assert "skill: Python" in request.user
    assert "Relevant resume entry" in request.user
