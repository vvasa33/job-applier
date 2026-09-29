"""Workday page interaction and field decisions. Decisions never invent answers, and nothing here submits an application."""

from jobhunter.apply.decisions import (
    ApplicantData,
    DecisionSource,
    FieldAction,
    FieldDecision,
    KnownFact,
    decide,
)
from jobhunter.apply.fields import ApplicationField, NavigationButton, WorkdayPage
from jobhunter.apply.runner import refuse_submit, resume_page, run_page
from jobhunter.apply.workday import WorkdayAdapter, WorkdayPageError, parse_workday_document

__all__ = [
    "ApplicantData",
    "ApplicationField",
    "DecisionSource",
    "FieldAction",
    "FieldDecision",
    "KnownFact",
    "NavigationButton",
    "WorkdayAdapter",
    "WorkdayPage",
    "WorkdayPageError",
    "decide",
    "parse_workday_document",
    "refuse_submit",
    "resume_page",
    "run_page",
]
