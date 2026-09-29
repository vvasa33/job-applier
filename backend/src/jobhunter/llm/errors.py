"""Errors from the LLM gateway. Messages must not include secrets or response bodies."""


class LLMError(Exception):
    """The gateway could not return a validated result."""


class LLMConfigError(LLMError):
    """LLM configuration is missing or not allowed."""


class ProviderError(LLMError):
    """One provider call failed."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
