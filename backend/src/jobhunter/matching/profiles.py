"""Inputs to matching. These are not database rows."""

from pydantic import BaseModel, Field

from jobhunter.domain.enums import CsRelevance, LocationClass
from jobhunter.resume.parser import ParsedResume


class JobProfile(BaseModel):
    title: str
    company: str
    locations: list[str] = Field(default_factory=list)
    location_class: LocationClass
    is_internship: bool | None
    cs_relevance: CsRelevance = CsRelevance.unknown
    description_text: str | None = None


class ResumeProfile(BaseModel):
    skills: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)

    def text(self) -> str:
        return "\n".join([*self.skills, *self.experience, *self.projects, *self.education])


def resume_profile_from_parsed(parsed: ParsedResume) -> ResumeProfile:
    grouped: dict[str, list[str]] = {"skills": [], "experience": [], "projects": [], "education": []}
    for section in parsed.sections:
        if section.kind in grouped:
            grouped[section.kind].extend(section.entries)
    return ResumeProfile(**grouped)
