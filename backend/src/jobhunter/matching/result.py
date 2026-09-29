"""Structured match output. There is no single numeric score."""

from typing import Literal

from pydantic import BaseModel, Field


class FilterFinding(BaseModel):
    name: str
    passed: bool
    reason: str


class DeterministicSignal(BaseModel):
    name: str
    detail: str


class LocationFit(BaseModel):
    preference: Literal["dmv", "us_remote", "us_other", "unknown", "non_us"]
    summary: str


class InternshipFit(BaseModel):
    status: Literal["internship", "not_internship", "unknown"]
    summary: str


class SemanticAnalysis(BaseModel):
    """Structured output required from any semantic matcher, including a fake one."""

    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    relevant_experience: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    explanation: str
    disqualifier: str | None = None
    evidence_quote: str | None = None


class MatchResult(BaseModel):
    assessment: str
    recommendation: Literal["apply", "maybe", "skip"]
    matched_skills: list[str]
    missing_skills: list[str]
    relevant_experience: list[str]
    location_fit: LocationFit
    internship_fit: InternshipFit
    concerns: list[str]
    explanation: str
    hard_filters: list[FilterFinding]
    deterministic_signals: list[DeterministicSignal]
    semantic: SemanticAnalysis | None = None
