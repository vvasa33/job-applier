"""Semantic analysis is an interface. This package does not call a model provider."""

from typing import Protocol

from jobhunter.matching.profiles import JobProfile, ResumeProfile
from jobhunter.matching.result import SemanticAnalysis


class SemanticMatcher(Protocol):
    def analyze(self, job: JobProfile, resume: ResumeProfile) -> SemanticAnalysis:
        """Return structured analysis. Implementations must not write the master resume."""


def verified_semantic(analysis: SemanticAnalysis, job: JobProfile) -> SemanticAnalysis:
    """Drop a disqualifier whose quote is not present in the job text."""
    quote = (analysis.evidence_quote or "").strip()
    description = job.description_text or ""
    if analysis.disqualifier and quote and quote.casefold() in description.casefold():
        return analysis
    if analysis.disqualifier:
        concerns = [
            *analysis.concerns,
            "A semantic disqualifier was ignored because its quote was not in the job text.",
        ]
        return analysis.model_copy(update={"disqualifier": None, "evidence_quote": None, "concerns": concerns})
    return analysis
