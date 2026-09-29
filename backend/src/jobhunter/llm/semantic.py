"""Adapter from LLMClient to the existing semantic matching interface."""

from jobhunter.llm.client import LLMClient
from jobhunter.matching.profiles import JobProfile, ResumeProfile
from jobhunter.matching.result import SemanticAnalysis


class LLMSemanticMatcher:
    """Satisfies SemanticMatcher. Matching still decides what to do with the result."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def analyze(self, job: JobProfile, resume: ResumeProfile) -> SemanticAnalysis:
        return self._client.semantic_match(job, resume).output
