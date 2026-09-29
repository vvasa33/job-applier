"""Structured results the model is allowed to return. None of these drive a browser."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jobhunter.matching.result import SemanticAnalysis


class JobAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    requirements: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)


class TailoredBullet(BaseModel):
    """One output bullet. source_ids name the master entries it is derived from."""

    model_config = ConfigDict(extra="ignore")

    text: str
    source_ids: list[str] = Field(default_factory=list)


class TailoringPlan(BaseModel):
    """Proposed bullets for deterministic code to validate and apply. The model never sees file paths."""

    model_config = ConfigDict(extra="ignore")

    bullets: list[TailoredBullet] = Field(default_factory=list)
    explanation: str = ""


class AnswerSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field_label: str
    suggested_value: str
    confidence: Literal["low", "medium", "high"]
    needs_human: bool
    reason: str


__all__ = ["AnswerSuggestion", "JobAnalysis", "SemanticAnalysis", "TailoredBullet", "TailoringPlan"]
