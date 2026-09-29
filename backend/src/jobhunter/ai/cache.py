"""Validated model outputs, keyed by what the prompt saw. The model is not part of the key on purpose."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jobhunter.ai.hashing import digest
from jobhunter.db.models import AICache

# Bump a purpose's version when its prompt or output schema changes, so old answers are not reused.
PROMPT_VERSIONS = {
    "job_analysis": "1",
    "semantic_matching": "1",
    "question_classification": "1",
    "question_suggestion": "1",
    "difficult_question": "1",
    "resume_tailoring": "1",
}


def prompt_version(purpose: str) -> str:
    return PROMPT_VERSIONS.get(purpose, "1")


def cache_key(purpose: str, fingerprint: dict) -> str:
    return digest({"purpose": purpose, "prompt_version": prompt_version(purpose), "input": fingerprint})


def lookup(session: Session, key: str) -> AICache | None:
    return session.scalar(select(AICache).where(AICache.cache_key == key))


def store(
    session: Session, key: str, *, purpose: str, model: str | None, output: dict, cost: Decimal, now: datetime
) -> None:
    """Keep the first answer if another process stored one for the same key meanwhile."""

    try:
        with session.begin_nested():
            session.add(
                AICache(
                    cache_key=key,
                    purpose=purpose,
                    prompt_version=prompt_version(purpose),
                    model=model,
                    output=output,
                    cost_usd=cost,
                    hits=0,
                    created_at=now,
                    last_used_at=now,
                )
            )
    except IntegrityError:
        pass
