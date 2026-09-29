"""External model gateway. Providers are replaceable. The API key stays in the environment."""

from jobhunter.llm.client import LLMClient, client_from_environment
from jobhunter.llm.semantic import LLMSemanticMatcher

__all__ = ["LLMClient", "LLMSemanticMatcher", "client_from_environment"]
