"""Workday page interaction, field decisions, and the submit gate. Decisions never invent answers."""

from jobhunter.apply.decisions import (
    ApplicantData,
    DecisionSource,
    FieldAction,
    FieldDecision,
    KnownFact,
    decide,
)
from jobhunter.apply.fields import ApplicationField, NavigationButton, WorkdayPage
from jobhunter.apply.runner import page_context, respond, resume_page, run_page, waiting_applications
from jobhunter.apply.submission import SubmitDecision, SubmitPolicy, authorize
from jobhunter.apply.workday import WorkdayAdapter, WorkdayPageError, parse_workday_document

__all__ = [
    "ApplicantData",
    "ApplicationField",
    "DecisionSource",
    "FieldAction",
    "FieldDecision",
    "KnownFact",
    "NavigationButton",
    "SubmitDecision",
    "SubmitPolicy",
    "WorkdayAdapter",
    "WorkdayPage",
    "WorkdayPageError",
    "authorize",
    "decide",
    "page_context",
    "parse_workday_document",
    "respond",
    "resume_page",
    "run_page",
    "waiting_applications",
]
