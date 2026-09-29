"""LLM settings that are allowed to leave the process boundary.

The API key is read from the environment or a gitignored .env file. It is not a
Settings field, so a TOML file or the frontend cannot supply it.
"""

import os
from decimal import Decimal

from jobhunter.llm.errors import LLMConfigError
from jobhunter.local_env import load_local_env

API_KEY_ENV = "JOBHUNTER_LLM_API_KEY"

# Approximate USD per 1,000,000 tokens. This is an estimate, not an invoice.
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


def llm_model() -> str:
    load_local_env()
    return os.environ.get("JOBHUNTER_LLM_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"


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


def approximate_cost_usd(input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
    if input_tokens is None and output_tokens is None:
        return None
    cost = Decimal(0)
    if input_tokens is not None:
        cost += Decimal(input_tokens) * _INPUT_USD_PER_MILLION / Decimal(1_000_000)
    if output_tokens is not None:
        cost += Decimal(output_tokens) * _OUTPUT_USD_PER_MILLION / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))
