"""The one way the application spends money on a model: cache first, then the daily budget, then the call.

Ledger and cache rows are written in their own session and committed at once, so a later rollback in the
caller never forgets a paid call. Callers must not hold uncommitted writes while calling in, because SQLite
allows only one writer.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from jobhunter.ai import cache, ledger
from jobhunter.ai.hashing import digest, job_content_hash
from jobhunter.domain.time import utcnow
from jobhunter.llm.client import LLMClient, LLMResult, client_from_environment
from jobhunter.llm.config import tier_for
from jobhunter.llm.errors import LLMError
from jobhunter.llm.schemas import TailoringPlan
from jobhunter.matching.profiles import JobProfile, ResumeProfile
from jobhunter.matching.result import SemanticAnalysis


class BudgetExceeded(LLMError):
    """Nonessential AI work was refused because it would exceed today's budget."""


class AIService:
    def __init__(
        self,
        sessions: Callable[[], Session],
        *,
        client: Callable[[], LLMClient] = client_from_environment,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sessions = sessions
        self._client_factory = client
        self._client: LLMClient | None = None
        self._unavailable: str | None = None
        self._now = now

    def client(self) -> LLMClient:
        if self._client is None:
            if self._unavailable is not None:
                raise LLMError(self._unavailable)
            try:
                self._client = self._client_factory()
            except LLMError as exc:
                self._unavailable = str(exc)
                raise
        return self._client

    def configured(self) -> bool:
        try:
            self.client()
        except LLMError:
            return False
        return True

    def budget(self) -> ledger.BudgetStatus:
        with self._sessions() as session:
            return ledger.budget_status(session, self._now())

    def can_spend(self, purpose: str) -> bool:
        """Whether nonessential work for this purpose fits today's budget. With no model there is nothing to spend."""

        if not self.configured():
            return True
        model = self.client().model_for(purpose)
        with self._sessions() as session:
            status = ledger.budget_status(session, self._now())
            return status.allows(ledger.estimate(session, purpose, model))

    def run[T: BaseModel](
        self,
        purpose: str,
        fingerprint: dict,
        schema: type[T],
        invoke: Callable[[LLMClient], LLMResult[T]],
        *,
        essential: bool,
        job_id: int | None = None,
        application_id: int | None = None,
    ) -> T:
        key = cache.cache_key(purpose, fingerprint)
        tier = tier_for(purpose)
        attribution = {"job_id": job_id, "application_id": application_id}
        with self._sessions() as session:
            hit = cache.lookup(session, key)
            if hit is not None:
                now = self._now()
                hit.hits += 1
                hit.last_used_at = now
                ledger.record(
                    session,
                    purpose=purpose,
                    tier=tier,
                    model=hit.model,
                    status="cached",
                    essential=essential,
                    saved=hit.cost_usd,
                    cache_key=key,
                    now=now,
                    **attribution,
                )
                session.commit()
                return schema.model_validate(hit.output)

            client = self.client()
            model = client.model_for(purpose)
            expected = ledger.estimate(session, purpose, model)
            status = ledger.budget_status(session, self._now())
            if not essential and not status.allows(expected):
                ledger.record(
                    session,
                    purpose=purpose,
                    tier=tier,
                    model=model,
                    status="refused",
                    essential=False,
                    cache_key=key,
                    detail=f"spent {status.spent} of {status.budget}; this call was estimated at {expected}",
                    now=self._now(),
                    **attribution,
                )
                session.commit()
                raise BudgetExceeded(
                    f"today's AI budget of ${status.budget} is used up (${status.spent} spent); {purpose} was skipped"
                )

        before = len(client.usage)
        try:
            result = invoke(client)
        except LLMError as exc:
            self._record_failure(client.usage[before:], purpose, tier, model, key, essential, str(exc), attribution)
            raise

        usage = result.usage
        cost = Decimal(usage.cost_usd) if usage.cost_usd is not None else expected
        with self._sessions() as session:
            now = self._now()
            ledger.record(
                session,
                purpose=purpose,
                tier=tier,
                model=usage.model,
                status="ok",
                essential=essential,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=cost,
                cache_key=key,
                now=now,
                **attribution,
            )
            cache.store(
                session, key, purpose=purpose, model=usage.model, output=result.output.model_dump(mode="json"), cost=cost, now=now
            )
            session.commit()
        return result.output

    def _record_failure(self, usages, purpose, tier, model, key, essential, message, attribution) -> None:
        with self._sessions() as session:
            now = self._now()
            if not usages:
                ledger.record(
                    session,
                    purpose=purpose,
                    tier=tier,
                    model=model,
                    status="error",
                    essential=essential,
                    cache_key=key,
                    detail=message,
                    now=now,
                    **attribution,
                )
            for usage in usages:
                ledger.record(
                    session,
                    purpose=purpose,
                    tier=tier,
                    model=usage.model,
                    status=usage.status,
                    essential=essential,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost=Decimal(usage.cost_usd) if usage.cost_usd is not None else None,
                    cache_key=key,
                    detail=message,
                    now=now,
                    **attribution,
                )
            session.commit()

    def attach_version(self, application_id: int, version_id: int) -> None:
        with self._sessions() as session:
            ledger.attach_version(session, application_id, version_id)
            session.commit()


class CachedSemanticMatcher:
    """Semantic matching on the cheap tier. The job part of the key is its content hash, so an unchanged
    posting is never analyzed twice against the same resume."""

    def __init__(self, ai: AIService, *, job_id: int | None = None, essential: bool = False) -> None:
        self._ai = ai
        self._job_id = job_id
        self._essential = essential

    def analyze(self, job: JobProfile, resume: ResumeProfile) -> SemanticAnalysis:
        return self._ai.run(
            "semantic_matching",
            {"job": job_content_hash(job), "resume": digest(resume.model_dump(mode="json"))},
            SemanticAnalysis,
            lambda client: client.semantic_match(job, resume),
            essential=self._essential,
            job_id=self._job_id,
        )


class CachedTailoringPlanner:
    """Resume tailoring on the strong tier. A retry after a failed compile reuses the paid plan."""

    def __init__(
        self, ai: AIService, *, job_id: int | None, application_id: int | None, essential: bool
    ) -> None:
        self._ai = ai
        self._job_id = job_id
        self._application_id = application_id
        self._essential = essential

    def plan(self, job: JobProfile, entries, requirements, evidence) -> TailoringPlan:
        return self._ai.run(
            "resume_tailoring",
            {
                "job": job_content_hash(job),
                "entries": digest([list(item) for item in entries]),
                "requirements": digest(list(requirements)),
                "evidence": digest(list(evidence)),
            },
            TailoringPlan,
            lambda client: client.tailor_resume(job, entries, requirements=requirements, evidence=evidence),
            essential=self._essential,
            job_id=self._job_id,
            application_id=self._application_id,
        )
