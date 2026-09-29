"""Vendor-neutral completion call. Callers never see an API key."""

from typing import Protocol

from pydantic import BaseModel


class CompletionRequest(BaseModel):
    purpose: str
    model: str
    system: str
    user: str
    json_schema_name: str


class CompletionResponse(BaseModel):
    content: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    name: str

    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        """Return JSON text. Must not log the API key or the prompt."""


class UsageRecord(BaseModel):
    purpose: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: str | None
    latency_ms: int
    attempts: int
    status: str


class FakeProvider:
    """Scripted provider for tests. It does not make network calls."""

    name = "fake"

    def __init__(self, contents: list[str], *, failures: list[Exception] | None = None) -> None:
        self._contents = list(contents)
        self._failures = list(failures or [])
        self.requests: list[CompletionRequest] = []
        self.timeouts: list[float] = []

    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        self.requests.append(request)
        self.timeouts.append(timeout_s)
        if self._failures:
            raise self._failures.pop(0)
        if not self._contents:
            raise RuntimeError("fake provider has no scripted response")
        content = self._contents.pop(0)
        return CompletionResponse(
            content=content,
            provider=self.name,
            model=request.model,
            input_tokens=120,
            output_tokens=40,
        )


class TokenlessProvider(FakeProvider):
    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        response = super().complete(request, timeout_s=timeout_s)
        return response.model_copy(update={"input_tokens": None, "output_tokens": None})
