"""LLM settings that are allowed to leave the process boundary.

The API key is read from the environment or a gitignored .env file. It is not a
Settings field, so a TOML file or the frontend cannot supply it.
"""

import os
from decimal import Decimal

from jobhunter.llm.errors import LLMConfigError
from jobhunter.local_env import load_local_env

API_KEY_ENV = "JOBHUNTER_LLM_API_KEY"

CHEAP = "cheap"
STRONG = "strong"
# Classification runs on the cheap tier. Tailoring and difficult questions use the strong tier.
PURPOSE_TIERS = {
    "job_analysis": CHEAP,
    "semantic_matching": CHEAP,
    "question_classification": CHEAP,
    "question_suggestion": CHEAP,
    "difficult_question": STRONG,
    "resume_tailoring": STRONG,
}
_DEFAULT_MODELS = {CHEAP: "gpt-4.1-nano", STRONG: "gpt-4.1"}

# Approximate USD per 1,000,000 input and output tokens. These are estimates, not an invoice.
_PRICES = {
    "gpt-4.1": (Decimal("2.00"), Decimal("8.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "gpt-4.1-nano": (Decimal("0.10"), Decimal("0.40")),
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
}
_INPUT_USD_PER_MILLION = Decimal("0.40")
_OUTPUT_USD_PER_MILLION = Decimal("1.60")


def api_key_from_environment() -> str:
    load_local_env()
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise LLMConfigError(f"{API_KEY_ENV} is not set")
    return key


def llm_base_url() -> str:
    load_local_env()
    return os.environ.get("JOBHUNTER_LLM_BASE_URL", "https://api.openai.com/v1").strip()


def tier_for(purpose: str) -> str:
    return PURPOSE_TIERS.get(purpose, STRONG)


def llm_model_for(tier: str) -> str:
    """JOBHUNTER_LLM_MODEL_CHEAP / _STRONG pick each tier. The older JOBHUNTER_LLM_MODEL sets the strong tier."""

    load_local_env()
    specific = os.environ.get(f"JOBHUNTER_LLM_MODEL_{tier.upper()}", "").strip()
    if specific:
        return specific
    if tier == STRONG:
        legacy = os.environ.get("JOBHUNTER_LLM_MODEL", "").strip()
        if legacy:
            return legacy
    return _DEFAULT_MODELS[tier]


def token_prices(model: str | None) -> tuple[Decimal, Decimal]:
    """Unknown models are priced like gpt-4.1-mini."""

    if model is None:
        return _INPUT_USD_PER_MILLION, _OUTPUT_USD_PER_MILLION
    name = model.strip().lower()
    for known in sorted(_PRICES, key=len, reverse=True):
        if name == known or name.startswith(f"{known}-20"):
            return _PRICES[known]
    return _INPUT_USD_PER_MILLION, _OUTPUT_USD_PER_MILLION


def llm_timeout_s() -> float:
    load_local_env()
    raw = os.environ.get("JOBHUNTER_LLM_TIMEOUT_S", "30").strip()
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise LLMConfigError("JOBHUNTER_LLM_TIMEOUT_S must be a number of seconds") from exc
    if timeout <= 0 or timeout > 120:
        raise LLMConfigError("JOBHUNTER_LLM_TIMEOUT_S must be between 0 and 120 seconds")
    return timeout


def llm_max_attempts() -> int:
    load_local_env()
    raw = os.environ.get("JOBHUNTER_LLM_MAX_ATTEMPTS", "3").strip()
    try:
        attempts = int(raw)
    except ValueError as exc:
        raise LLMConfigError("JOBHUNTER_LLM_MAX_ATTEMPTS must be an integer") from exc
    if attempts < 1 or attempts > 5:
        raise LLMConfigError("JOBHUNTER_LLM_MAX_ATTEMPTS must be from 1 to 5")
    return attempts


def approximate_cost_usd(
    input_tokens: int | None,
    output_tokens: int | None,
    model: str | None = None,
) -> Decimal | None:
    if input_tokens is None and output_tokens is None:
        return None
    input_price, output_price = token_prices(model)
    cost = Decimal(0)
    if input_tokens is not None:
        cost += Decimal(input_tokens) * input_price / Decimal(1_000_000)
    if output_tokens is not None:
        cost += Decimal(output_tokens) * output_price / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))
