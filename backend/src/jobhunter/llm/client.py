"""Gateway for structured model calls. It returns data. It does not browse or write files."""

import json
import logging
import time
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from jobhunter.llm.config import approximate_cost_usd, llm_max_attempts, llm_model, llm_timeout_s
from jobhunter.llm.errors import LLMError, ProviderError
from jobhunter.llm.http import http_provider_from_environment
from jobhunter.llm.profiles import job_text, resume_text
from jobhunter.llm.provider import CompletionRequest, LLMProvider, UsageRecord
from jobhunter.llm.schemas import AnswerSuggestion, JobAnalysis, SemanticAnalysis, TailoringPlan
from jobhunter.matching.profiles import JobProfile, ResumeProfile

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_SYSTEM = (
    "Return one JSON object matching the requested schema. "
    "Do not request tools, browser actions, or file writes."
)

TAILORING_RULES = "\n".join(
    [
        "You are choosing and rewording bullets from one person's master resume for one job.",
        "Rules:",
        "- Never invent experience.",
        "- Never invent metrics.",
        "- Never invent employers.",
        "- Never invent dates.",
        "- Never invent skills.",
        "- Never invent education.",
        "- Never claim experience that cannot be traced to the master resume.",
        "- You may rewrite, reorder, remove, combine, and emphasize truthful information.",
        "- Every bullet must list, in source_ids, the ids of the master entries it is based on.",
        "- Only combine entries that belong to the same employer, project, or section entry.",
        "- Missing skills from the job must stay missing unless a master entry already states them.",
        "- Return plain text. Do not return LaTeX commands or braces.",
        "Bullets you return for an entry replace that entry's bullets, in the order you list them.",
        "Master bullets you leave out of an entry you touched are removed.",
    ]
)


class LLMResult[T: BaseModel]:
    def __init__(self, output: T, usage: UsageRecord) -> None:
        self.output = output
        self.usage = usage


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str | None = None,
        timeout_s: float | None = None,
        max_attempts: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._model = model if model is not None else llm_model()
        self._timeout_s = timeout_s if timeout_s is not None else llm_timeout_s()
        self._max_attempts = max_attempts if max_attempts is not None else llm_max_attempts()
        self._sleep = sleep
        self.usage: list[UsageRecord] = []

    def analyze_job(self, job: JobProfile) -> LLMResult[JobAnalysis]:
        return self._call(
            purpose="job_analysis",
            schema=JobAnalysis,
            user=f"Job title: {job.title}\nCompany: {job.company}\nDescription:\n{job.description_text or ''}",
        )

    def semantic_match(self, job: JobProfile, resume: ResumeProfile) -> LLMResult[SemanticAnalysis]:
        return self._call(
            purpose="semantic_matching",
            schema=SemanticAnalysis,
            user=f"Job:\n{job_text(job)}\n\nResume:\n{resume_text(resume)}",
        )

    def tailor_resume(
        self,
        job: JobProfile,
        entries: list[tuple[str, str]],
        *,
        requirements: list[str] | None = None,
        evidence: list[str] | None = None,
    ) -> LLMResult[TailoringPlan]:
        lines = [f"{entry_id}: {text}" for entry_id, text in entries]
        user = (
            f"Job:\n{job_text(job)}\n\n"
            f"Job requirements:\n{_bullets(requirements)}\n\n"
            f"Match evidence:\n{_bullets(evidence)}\n\n"
            "Master resume entries (id: [section / entry] text):\n" + "\n".join(lines)
        )
        return self._call(
            purpose="resume_tailoring",
            schema=TailoringPlan,
            user=user,
            instructions=TAILORING_RULES,
        )

    def suggest_answer(
        self,
        *,
        field_label: str,
        field_type: str,
        job_title: str,
        context: str,
    ) -> LLMResult[AnswerSuggestion]:
        return self._call(
            purpose="question_suggestion",
            schema=AnswerSuggestion,
            user=(
                f"Field label: {field_label}\n"
                f"Field type: {field_type}\n"
                f"Job title: {job_title}\n"
                f"Context:\n{context}"
            ),
        )

    def _call(self, *, purpose: str, schema: type[T], user: str, instructions: str | None = None) -> LLMResult[T]:
        system = _SYSTEM if instructions is None else f"{_SYSTEM}\n\n{instructions}"
        shape = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        request = CompletionRequest(
            purpose=purpose,
            model=self._model,
            system=system,
            user=f"{user}\n\nJSON schema:\n{shape}",
            json_schema_name=schema.__name__,
        )
        started = time.perf_counter()
        response, attempts = self._complete_with_retries(request)
        try:
            output = schema.model_validate(json.loads(response.content))
        except (json.JSONDecodeError, ValidationError) as exc:
            self._record(request, response, started, attempts=attempts, status="invalid")
            raise LLMError(f"{purpose} response did not match {schema.__name__}") from exc
        usage = self._record(request, response, started, attempts=attempts, status="ok")
        return LLMResult(output=output, usage=usage)

    def _complete_with_retries(self, request: CompletionRequest):
        last: ProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._provider.complete(request, timeout_s=self._timeout_s), attempt
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == self._max_attempts:
                    logger.info(
                        "llm purpose=%s provider=%s model=%s status=error attempts=%s retryable=%s",
                        request.purpose,
                        self._provider.name,
                        request.model,
                        attempt,
                        exc.retryable,
                    )
                    raise LLMError(f"{request.purpose} failed after {attempt} attempts") from exc
                self._sleep(min(1.0, 0.25 * attempt))
        raise LLMError(f"{request.purpose} failed") from last

    def _record(self, request, response, started: float, *, attempts: int, status: str) -> UsageRecord:
        cost = approximate_cost_usd(response.input_tokens, response.output_tokens)
        usage = UsageRecord(
            purpose=request.purpose,
            provider=response.provider,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=None if cost is None else format(cost, "f"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
            status=status,
        )
        self.usage.append(usage)
        logger.info(
            "llm purpose=%s provider=%s model=%s status=%s attempts=%s input_tokens=%s output_tokens=%s cost_usd=%s latency_ms=%s",
            usage.purpose,
            usage.provider,
            usage.model,
            usage.status,
            usage.attempts,
            usage.input_tokens,
            usage.output_tokens,
            usage.cost_usd,
            usage.latency_ms,
        )
        return usage


def _bullets(items: list[str] | None) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- none"


def client_from_environment() -> LLMClient:
    return LLMClient(http_provider_from_environment())
