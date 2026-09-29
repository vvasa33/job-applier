"""Combine hard filters, deterministic signals, and optional semantic analysis."""

from jobhunter.matching.filters import hard_filters
from jobhunter.matching.profiles import JobProfile, ResumeProfile
from jobhunter.matching.result import MatchResult, SemanticAnalysis
from jobhunter.matching.semantic import SemanticMatcher, verified_semantic
from jobhunter.matching.signals import (
    deterministic_signals,
    internship_fit,
    location_fit,
    relevant_experience,
    skill_overlap,
)


def match_job(
    job: JobProfile,
    resume: ResumeProfile,
    semantic: SemanticMatcher | None = None,
) -> MatchResult:
    filters = hard_filters(job)
    place = location_fit(job)
    internship = internship_fit(job)
    matched, missing = skill_overlap(job, resume)
    experience = relevant_experience(job, resume, matched)
    signals = deterministic_signals(
        job,
        matched_skills=matched,
        missing_skills=missing,
        experience=experience,
        location=place,
        internship=internship,
    )
    analysis = verified_semantic(semantic.analyze(job, resume), job) if semantic is not None else None
    if analysis is not None:
        matched, missing, experience = _merge_semantic(analysis, resume, matched, missing, experience)

    concerns = _concerns(filters, place, internship, missing, analysis)
    failed = [item for item in filters if not item.passed]
    if failed or (analysis is not None and analysis.disqualifier):
        recommendation = "skip"
        assessment = "Rejected."
    elif matched or experience:
        recommendation = "apply"
        assessment = "Promising overlap."
    else:
        recommendation = "maybe"
        assessment = "Possible, with limited overlap."

    explanation = _explanation(failed, place, internship, matched, missing, experience, analysis)
    return MatchResult(
        assessment=assessment,
        recommendation=recommendation,
        matched_skills=matched,
        missing_skills=missing,
        relevant_experience=experience,
        location_fit=place,
        internship_fit=internship,
        concerns=concerns,
        explanation=explanation,
        hard_filters=filters,
        deterministic_signals=signals,
        semantic=analysis,
    )


def _merge_semantic(
    analysis: SemanticAnalysis,
    resume: ResumeProfile,
    matched: list[str],
    missing: list[str],
    experience: list[str],
) -> tuple[list[str], list[str], list[str]]:
    resume_text = resume.text().casefold()
    for skill in analysis.matched_skills:
        if skill.casefold() in resume_text and skill not in matched:
            matched.append(skill)
    for skill in analysis.missing_skills:
        if skill not in missing and skill not in matched:
            missing.append(skill)
    for entry in analysis.relevant_experience:
        if entry not in experience:
            experience.append(entry)
    return matched, missing, experience


def _concerns(filters, place, internship, missing, analysis: SemanticAnalysis | None) -> list[str]:
    concerns = [item.reason for item in filters if not item.passed]
    if place.preference == "unknown":
        concerns.append(place.summary)
    if internship.status == "unknown":
        concerns.append(internship.summary)
    if missing:
        concerns.append(f"The posting names skills that are not on the resume: {', '.join(missing)}.")
    if analysis is not None:
        concerns.extend(analysis.concerns)
        if analysis.disqualifier:
            concerns.append(analysis.disqualifier)
    return concerns


def _explanation(failed, place, internship, matched, missing, experience, analysis: SemanticAnalysis | None) -> str:
    sentences: list[str] = []
    if failed:
        sentences.append("Hard filters rejected the posting. " + " ".join(item.reason for item in failed))
    else:
        sentences.append("Hard filters passed: US location, internship relevance, and computing role.")
    sentences.append(place.summary)
    sentences.append(internship.summary)
    if matched:
        sentences.append(f"Deterministic skill overlap: {', '.join(matched)}.")
    else:
        sentences.append("Deterministic matching found no shared listed skills.")
    if missing:
        sentences.append(f"Missing skills: {', '.join(missing)}.")
    if experience:
        sentences.append(f"Relevant experience: {experience[0]}.")
    else:
        sentences.append("No experience entry was tied to the posting.")
    if analysis is None:
        sentences.append("Semantic analysis was not run.")
    else:
        sentences.append(analysis.explanation)
        if analysis.disqualifier:
            sentences.append(f"Semantic disqualifier: {analysis.disqualifier}.")
    return " ".join(sentences)
