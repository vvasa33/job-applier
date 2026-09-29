import pytest
from pydantic import ValidationError

from jobhunter.domain.enums import CsRelevance, LocationClass
from jobhunter.matching.profiles import JobProfile, ResumeProfile
from jobhunter.matching.result import MatchResult, SemanticAnalysis
from jobhunter.matching.service import match_job

RESUME = ResumeProfile(
    skills=["Python, SQL, Linux"],
    experience=["Built a data pipeline in Python for weekly reporting."],
    projects=["Local job hunter in Python."],
    education=["B.S. Computer Science"],
)


class FakeSemantic:
    def __init__(self, analysis: SemanticAnalysis) -> None:
        self.analysis = analysis
        self.calls = 0

    def analyze(self, job: JobProfile, resume: ResumeProfile) -> SemanticAnalysis:
        self.calls += 1
        return self.analysis


def _job(**overrides) -> JobProfile:
    fields = {
        "title": "Software Engineering Intern",
        "company": "Northwind",
        "locations": ["McLean, VA"],
        "location_class": LocationClass.dmv,
        "is_internship": True,
        "cs_relevance": CsRelevance.relevant,
        "description_text": "Build services in Python. Rust is a plus.",
    }
    fields.update(overrides)
    return JobProfile(**fields)


def test_match_result_has_no_numeric_score() -> None:
    assert "score" not in MatchResult.model_fields
    result = match_job(_job(), RESUME)
    assert result.recommendation == "apply"
    assert result.assessment
    assert "Python" in result.explanation or "python" in result.explanation


def test_hard_filters_reject_non_us_non_internship_and_non_cs() -> None:
    abroad = match_job(_job(location_class=LocationClass.non_us, locations=["London, United Kingdom"]), RESUME)
    assert abroad.recommendation == "skip"
    assert any(item.name == "us_location" and not item.passed for item in abroad.hard_filters)
    assert "outside the US" in abroad.explanation

    full_time = match_job(_job(is_internship=False), RESUME)
    assert full_time.recommendation == "skip"
    assert full_time.internship_fit.status == "not_internship"

    sales = match_job(
        _job(title="Account Executive", cs_relevance=CsRelevance.unknown, description_text="Own a quota."),
        RESUME,
    )
    assert sales.recommendation == "skip"
    assert any(item.name == "cs_role" and not item.passed for item in sales.hard_filters)


def test_unknown_location_and_role_stay_in_recall() -> None:
    result = match_job(
        _job(
            title="Program Intern",
            location_class=LocationClass.unknown,
            locations=["Remote"],
            is_internship=None,
            cs_relevance=CsRelevance.unknown,
            description_text="Help the team.",
        ),
        RESUME,
    )
    assert result.recommendation == "maybe"
    assert all(item.passed for item in result.hard_filters)
    assert result.location_fit.preference == "unknown"
    assert result.semantic is None
    assert "Semantic analysis was not run." in result.explanation


def test_location_preferences_are_explained() -> None:
    dmv = match_job(_job(), RESUME)
    remote = match_job(_job(location_class=LocationClass.us_remote, locations=["Remote - United States"]), RESUME)
    other = match_job(_job(location_class=LocationClass.us_other, locations=["Austin, Texas"]), RESUME)
    assert dmv.location_fit.preference == "dmv"
    assert "preferred" in dmv.location_fit.summary
    assert remote.location_fit.preference == "us_remote"
    assert "preferred" in remote.location_fit.summary
    assert other.location_fit.preference == "us_other"
    assert "acceptable" in other.location_fit.summary
    assert "python" in dmv.matched_skills
    assert "rust" in dmv.missing_skills
    assert any("Python" in entry for entry in dmv.relevant_experience)
    assert any("rust" in concern.casefold() for concern in dmv.concerns)


def test_fake_semantic_output_is_structured_and_quote_checked() -> None:
    unsupported = FakeSemantic(
        SemanticAnalysis(
            matched_skills=["COBOL"],
            missing_skills=["Spark"],
            relevant_experience=["Campus research project."],
            concerns=["Graduation date is unclear."],
            explanation="The posting is adjacent to the resume.",
            disqualifier="Requires US citizenship.",
            evidence_quote="must be a citizen",
        )
    )
    kept = match_job(_job(description_text="Build services in Python."), RESUME, semantic=unsupported)
    assert unsupported.calls == 1
    assert kept.recommendation != "skip"
    assert "COBOL" not in kept.matched_skills
    assert "Spark" in kept.missing_skills
    assert "Campus research project." in kept.relevant_experience
    assert kept.semantic is not None
    assert kept.semantic.disqualifier is None
    assert any("not in the job text" in concern for concern in kept.concerns)

    quoted = FakeSemantic(
        SemanticAnalysis(
            explanation="Citizenship is required.",
            disqualifier="Requires US citizenship.",
            evidence_quote="U.S. citizenship required",
        )
    )
    rejected = match_job(
        _job(description_text="U.S. citizenship required for this internship."),
        RESUME,
        semantic=quoted,
    )
    assert rejected.recommendation == "skip"
    assert rejected.semantic is not None
    assert rejected.semantic.disqualifier == "Requires US citizenship."
    assert "Semantic disqualifier" in rejected.explanation


def test_semantic_analysis_rejects_unstructured_output() -> None:
    with pytest.raises(ValidationError):
        SemanticAnalysis.model_validate({"explanation": "ok", "matched_skills": "python"})
