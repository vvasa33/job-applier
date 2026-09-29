"""OpenAI-compatible HTTP provider. The rest of the app depends on LLMProvider, not this module."""

import json

import httpx

from jobhunter.llm.errors import ProviderError
from jobhunter.llm.provider import CompletionRequest, CompletionResponse


class HttpLLMProvider:
    """One chat-completions call. No tools, and the API key is never logged."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        name: str = "openai-compatible",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.Client(transport=transport)

    def __repr__(self) -> str:
        return f"HttpLLMProvider(name={self.name!r}, base_url={self.base_url!r})"

    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        payload = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                },
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("provider request timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError("provider request failed", retryable=True) from exc

        if response.status_code in {429, 500, 502, 503, 504}:
            raise ProviderError(f"provider request failed with HTTP {response.status_code}", retryable=True)
        if response.status_code != 200:
            raise ProviderError(f"provider request failed with HTTP {response.status_code}", retryable=False)

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("provider response was missing message content", retryable=False) from exc
        if not isinstance(content, str):
            raise ProviderError("provider response content was not text", retryable=False)
        usage = body.get("usage") if isinstance(body, dict) else None
        input_tokens = _token_count(usage, "prompt_tokens")
        output_tokens = _token_count(usage, "completion_tokens")
        return CompletionResponse(
            content=content,
            provider=self.name,
            model=request.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def http_provider_from_environment(*, transport: httpx.BaseTransport | None = None) -> HttpLLMProvider:
    from jobhunter.llm.config import api_key_from_environment, llm_base_url

    return HttpLLMProvider(
        api_key=api_key_from_environment(),
        base_url=llm_base_url(),
        transport=transport,
    )


def _token_count(usage: object, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if isinstance(value, int) and value >= 0:
        return value
    return None
