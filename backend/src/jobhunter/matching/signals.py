"""Deterministic comparisons between a job and a resume."""

import re

from jobhunter.domain.enums import LocationClass
from jobhunter.matching.profiles import JobProfile, ResumeProfile
from jobhunter.matching.result import DeterministicSignal, InternshipFit, LocationFit

_SKILLS = (
    "c++",
    "c#",
    "machine learning",
    "typescript",
    "javascript",
    "kubernetes",
    "pytorch",
    "tensorflow",
    "postgres",
    "postgresql",
    "python",
    "java",
    "golang",
    "rust",
    "react",
    "node",
    "aws",
    "sql",
    "linux",
    "docker",
    "git",
    "spark",
    "hadoop",
    "android",
    "ios",
)

_LOCATION_FIT = {
    LocationClass.dmv: (
        "dmv",
        "DMV is a preferred location.",
    ),
    LocationClass.us_remote: (
        "us_remote",
        "US remote is a preferred location.",
    ),
    LocationClass.us_other: (
        "us_other",
        "Another US location is acceptable.",
    ),
    LocationClass.unknown: (
        "unknown",
        "Location preference is unknown because the posting is not confirmed as US.",
    ),
    LocationClass.non_us: (
        "non_us",
        "The location is outside the preferred US set.",
    ),
}


def location_fit(job: JobProfile) -> LocationFit:
    preference, summary = _LOCATION_FIT[job.location_class]
    return LocationFit(preference=preference, summary=summary)


def internship_fit(job: JobProfile) -> InternshipFit:
    if job.is_internship is True:
        return InternshipFit(status="internship", summary="The posting is an internship.")
    if job.is_internship is False:
        return InternshipFit(status="not_internship", summary="The posting is not an internship.")
    return InternshipFit(status="unknown", summary="The posting does not say whether it is an internship.")


def skill_overlap(job: JobProfile, resume: ResumeProfile) -> tuple[list[str], list[str]]:
    job_text = _job_text(job)
    resume_text = resume.text().casefold()
    asked = [skill for skill in _SKILLS if _contains(job_text, skill)]
    matched = [skill for skill in asked if _contains(resume_text, skill)]
    missing = [skill for skill in asked if skill not in matched]
    return matched, missing


def relevant_experience(job: JobProfile, resume: ResumeProfile, matched_skills: list[str]) -> list[str]:
    title_tokens = {token for token in re.findall(r"[a-z]{4,}", job.title.casefold()) if token not in _TITLE_NOISE}
    found: list[str] = []
    for entry in (*resume.experience, *resume.projects):
        folded = entry.casefold()
        if any(_contains(folded, skill) for skill in matched_skills) or any(token in folded for token in title_tokens):
            found.append(entry)
    return found


def deterministic_signals(
    job: JobProfile,
    *,
    matched_skills: list[str],
    missing_skills: list[str],
    experience: list[str],
    location: LocationFit,
    internship: InternshipFit,
) -> list[DeterministicSignal]:
    skill_detail = (
        f"Matched skills: {', '.join(matched_skills)}."
        if matched_skills
        else "No listed skill from the posting was found on the resume."
    )
    missing_detail = (
        f"Skills named by the posting and absent from the resume: {', '.join(missing_skills)}."
        if missing_skills
        else "No extra posting skills were missing from the resume."
    )
    experience_detail = (
        f"{len(experience)} experience or project entries overlap the posting."
        if experience
        else "No experience or project entry overlaps the posting title or matched skills."
    )
    return [
        DeterministicSignal(name="location", detail=location.summary),
        DeterministicSignal(name="internship", detail=internship.summary),
        DeterministicSignal(name="skills", detail=skill_detail),
        DeterministicSignal(name="missing_skills", detail=missing_detail),
        DeterministicSignal(name="experience", detail=experience_detail),
    ]


def _job_text(job: JobProfile) -> str:
    parts = [job.title, *job.locations]
    if job.description_text:
        parts.append(job.description_text)
    return "\n".join(parts).casefold()


def _contains(text: str, skill: str) -> bool:
    return re.search(rf"(?<![a-z0-9#+]){re.escape(skill)}(?![a-z0-9#+])", text) is not None


_TITLE_NOISE = {"intern", "internship", "engineer", "engineering", "software", "developer", "summer", "fall"}
