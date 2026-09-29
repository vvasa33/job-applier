"""Estimated AI spend, recorded per call and attributed to a job, application, resume version, and day."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from jobhunter.db.models import AIUsage, UserSettings
from jobhunter.domain.time import utcnow
from jobhunter.llm.config import token_prices

ZERO = Decimal(0)
# Typical prompt and response sizes, used to estimate a call before any history exists.
_TYPICAL_TOKENS = {
    "job_analysis": (1500, 400),
    "semantic_matching": (2500, 300),
    "question_classification": (600, 100),
    "question_suggestion": (1500, 250),
    "difficult_question": (2500, 500),
    "resume_tailoring": (5000, 1500),
}


def local_day(moment: datetime | None = None) -> str:
    """Budgets reset at local midnight on this machine."""

    return (moment or utcnow()).astimezone().date().isoformat()


def record(
    session: Session,
    *,
    purpose: str,
    tier: str,
    status: str,
    essential: bool,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost: Decimal | None = None,
    saved: Decimal | None = None,
    cache_key: str | None = None,
    job_id: int | None = None,
    application_id: int | None = None,
    detail: str | None = None,
    now: datetime | None = None,
) -> AIUsage:
    moment = now or utcnow()
    row = AIUsage(
        created_at=moment,
        day=local_day(moment),
        purpose=purpose,
        tier=tier,
        model=model,
        status=status,
        essential=essential,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost or ZERO,
        saved_usd=saved or ZERO,
        cache_key=cache_key,
        job_id=job_id,
        application_id=application_id,
        detail=None if detail is None else detail[:500],
    )
    session.add(row)
    session.flush()
    return row


def spent_on(session: Session, day: str) -> Decimal:
    return Decimal(session.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0)).where(AIUsage.day == day)))


def total_for(
    session: Session,
    *,
    job_id: int | None = None,
    application_id: int | None = None,
    resume_version_id: int | None = None,
) -> Decimal:
    query = select(func.coalesce(func.sum(AIUsage.cost_usd), 0))
    if job_id is not None:
        query = query.where(AIUsage.job_id == job_id)
    if application_id is not None:
        query = query.where(AIUsage.application_id == application_id)
    if resume_version_id is not None:
        query = query.where(AIUsage.resume_version_id == resume_version_id)
    return Decimal(session.scalar(query))


def attach_version(session: Session, application_id: int, version_id: int) -> None:
    """Tailoring calls happen before the version exists; link them once it does."""

    session.execute(
        update(AIUsage)
        .where(
            AIUsage.application_id == application_id,
            AIUsage.purpose == "resume_tailoring",
            AIUsage.resume_version_id.is_(None),
        )
        .values(resume_version_id=version_id)
    )
    session.flush()


@dataclass(frozen=True)
class DayTotal:
    day: str
    cost: Decimal
    saved: Decimal
    calls: int
    cached: int
    refused: int


def daily(session: Session, days: int = 14) -> list[DayTotal]:
    rows = session.execute(
        select(
            AIUsage.day,
            func.sum(AIUsage.cost_usd),
            func.sum(AIUsage.saved_usd),
            func.sum(func.iif(AIUsage.status.in_(("ok", "invalid", "error")), 1, 0)),
            func.sum(func.iif(AIUsage.status == "cached", 1, 0)),
            func.sum(func.iif(AIUsage.status == "refused", 1, 0)),
        )
        .group_by(AIUsage.day)
        .order_by(AIUsage.day.desc())
        .limit(days)
    ).all()
    return [
        DayTotal(day=day, cost=Decimal(cost), saved=Decimal(saved), calls=int(calls), cached=int(cached), refused=int(refused))
        for day, cost, saved, calls, cached, refused in rows
    ]


def by_purpose(session: Session, day: str) -> dict[str, Decimal]:
    rows = session.execute(
        select(AIUsage.purpose, func.sum(AIUsage.cost_usd)).where(AIUsage.day == day).group_by(AIUsage.purpose)
    ).all()
    return {purpose: Decimal(cost) for purpose, cost in rows}


def estimate(session: Session, purpose: str, model: str | None) -> Decimal:
    """The average of recent paid calls for this purpose, or a typical size when there is no history."""

    recent = session.scalars(
        select(AIUsage.cost_usd)
        .where(AIUsage.purpose == purpose, AIUsage.status == "ok", AIUsage.model == model)
        .order_by(AIUsage.id.desc())
        .limit(20)
    ).all()
    if recent:
        return (sum((Decimal(item) for item in recent), ZERO) / len(recent)).quantize(Decimal("0.000001"))
    input_tokens, output_tokens = _TYPICAL_TOKENS.get(purpose, (3000, 800))
    input_price, output_price = token_prices(model)
    return ((input_tokens * input_price + output_tokens * output_price) / Decimal(1_000_000)).quantize(
        Decimal("0.000001")
    )


@dataclass(frozen=True)
class BudgetStatus:
    day: str
    spent: Decimal
    budget: Decimal

    @property
    def remaining(self) -> Decimal:
        return max(ZERO, self.budget - self.spent)

    @property
    def exceeded(self) -> bool:
        return self.spent >= self.budget

    def allows(self, cost: Decimal) -> bool:
        return self.spent + cost <= self.budget


def budget_status(session: Session, now: datetime | None = None) -> BudgetStatus:
    day = local_day(now)
    settings = session.get(UserSettings, 1)
    budget = Decimal(settings.daily_llm_budget_usd) if settings is not None else ZERO
    return BudgetStatus(day=day, spent=spent_on(session, day), budget=budget)
