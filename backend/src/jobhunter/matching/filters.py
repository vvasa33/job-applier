"""Hard filters. Unknown values pass so matching stays broad."""

import re

from jobhunter.domain.enums import CsRelevance, LocationClass
from jobhunter.matching.profiles import JobProfile
from jobhunter.matching.result import FilterFinding

_NON_CS_ROLES = (
    "account executive",
    "sales",
    "recruiter",
    "talent acquisition",
    "marketing",
    "nurse",
    "accountant",
    "cashier",
    "barista",
    "attorney",
    "lawyer",
    "pharmacist",
    "receptionist",
    "warehouse",
)


def hard_filters(job: JobProfile) -> list[FilterFinding]:
    return [
        _us_location(job),
        _internship(job),
        _cs_role(job),
    ]


def _us_location(job: JobProfile) -> FilterFinding:
    if job.location_class is LocationClass.non_us:
        place = ", ".join(job.locations) or "a non-US location"
        return FilterFinding(name="us_location", passed=False, reason=f"Location is outside the US: {place}.")
    if job.location_class is LocationClass.unknown:
        return FilterFinding(
            name="us_location",
            passed=True,
            reason="Location is not confirmed as US, so it is kept for review.",
        )
    return FilterFinding(name="us_location", passed=True, reason="Location is in the US or US remote.")


def _internship(job: JobProfile) -> FilterFinding:
    if job.is_internship is False:
        return FilterFinding(name="internship", passed=False, reason="The posting is not an internship.")
    if job.is_internship is None:
        return FilterFinding(
            name="internship",
            passed=True,
            reason="Internship status is unknown, so the posting is kept.",
        )
    return FilterFinding(name="internship", passed=True, reason="The posting is an internship.")


def _cs_role(job: JobProfile) -> FilterFinding:
    if job.cs_relevance is CsRelevance.not_relevant or _obvious_non_cs(job.title):
        if job.cs_relevance is CsRelevance.relevant:
            return FilterFinding(
                name="cs_role",
                passed=True,
                reason="The title also matches computing work, so it is kept.",
            )
        return FilterFinding(
            name="cs_role",
            passed=False,
            reason=f"The role looks unrelated to computing: {job.title}.",
        )
    if job.cs_relevance is CsRelevance.unknown:
        return FilterFinding(
            name="cs_role",
            passed=True,
            reason="Computing relevance is unclear, so the posting is kept.",
        )
    return FilterFinding(name="cs_role", passed=True, reason="The role looks related to computing.")


def _obvious_non_cs(title: str) -> bool:
    folded = title.casefold()
    return any(re.search(rf"(?<![a-z]){re.escape(role)}(?![a-z])", folded) for role in _NON_CS_ROLES)
