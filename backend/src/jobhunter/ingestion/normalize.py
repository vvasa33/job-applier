import re
from dataclasses import dataclass
from datetime import datetime

from jobhunter.domain.enums import CsRelevance, LocationClass
from jobhunter.domain.identity import canonical_url, make_dedup_key, normalize_name
from jobhunter.ingestion.ports import SourceIdentity
from jobhunter.ingestion.raw import RawJob

_INTERN = re.compile(r"\b(?:intern(?:ship)?s?|co-?ops?)\b|\b(?:summer|fall|spring|winter)\s+20\d{2}\b", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_TERM = re.compile(r"\b(summer|fall|spring|winter)\s+(20\d{2})\b", re.IGNORECASE)
_CS_KEYWORDS = (
    "software",
    "swe",
    "developer",
    "data",
    "ml",
    "ai",
    "security",
    "infrastructure",
    "cloud",
    "backend",
    "frontend",
    "full-stack",
    "fullstack",
    "full stack",
    "embedded",
    "research",
    "quant",
)
_DMV_PHRASES = (
    "washington, dc",
    "washington dc",
    "district of columbia",
    "mclean",
    "bethesda",
    "rockville",
    "reston",
    "herndon",
    "tysons",
    "chantilly",
    "college park",
    "silver spring",
    "arlington, va",
    "alexandria, va",
    "fairfax",
    "loudoun",
    "montgomery county",
    "prince george",
    "anne arundel",
    "howard county",
)
_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "ia", "id", "il", "in",
    "ks", "ky", "la", "ma", "md", "me", "mi", "mn", "mo", "ms", "mt", "nc", "nd", "ne", "nh",
    "nj", "nm", "nv", "ny", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "va",
    "vt", "wa", "wi", "wv", "wy", "dc",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
}
_NON_US = (
    "canada",
    "united kingdom",
    "uk",
    "london",
    "england",
    "germany",
    "france",
    "india",
    "ireland",
    "australia",
    "mexico",
    "singapore",
)
_LOCATION_RANK = {
    LocationClass.non_us: 0,
    LocationClass.unknown: 1,
    LocationClass.us_other: 2,
    LocationClass.us_remote: 3,
    LocationClass.dmv: 4,
}


class NormalizationError(ValueError):
    """A valid raw job still cannot become a canonical job."""


@dataclass(frozen=True)
class NormalizedJob:
    """Canonical fields ready to store. This is not a database row."""

    title: str
    normalized_title: str
    company: str
    normalized_company: str
    url: str
    canonical_url: str
    external_id: str
    ats_type: str
    board_key: str
    locations: tuple[str, ...]
    location_class: LocationClass
    is_internship: bool | None
    cs_relevance: CsRelevance
    term: str | None
    requisition_id: str | None
    description_text: str | None
    posted_at: datetime | None
    dedup_key: str
    raw: dict


def normalize(raw: RawJob, source: SourceIdentity) -> NormalizedJob:
    company = (raw.company or source.company or "").strip()
    if not company:
        raise NormalizationError("company is required")
    title = _WHITESPACE.sub(" ", raw.title).strip()
    locations = tuple(_clean_location(item) for item in raw.locations if item and item.strip())
    description = _plain_text(raw.description)
    term = _canonical_term(raw.term) or _canonical_term(description) or _canonical_term(title)
    requisition = (raw.requisition_id or "").strip() or None
    return NormalizedJob(
        title=title,
        normalized_title=normalize_name(title),
        company=company,
        normalized_company=normalize_name(company),
        url=raw.url,
        canonical_url=canonical_url(raw.url),
        external_id=raw.external_id,
        ats_type=source.ats_type,
        board_key=source.board_key,
        locations=locations,
        location_class=best_location_class(locations),
        is_internship=detect_internship(title, description),
        cs_relevance=detect_cs_relevance(title),
        term=term,
        requisition_id=requisition,
        description_text=description,
        posted_at=raw.posted_at,
        dedup_key=make_dedup_key(
            company=company,
            requisition_id=requisition,
            apply_url=raw.url,
            title=title,
            term=term,
        ),
        raw=dict(raw.raw),
    )


def detect_internship(title: str, description: str | None) -> bool | None:
    text = title if not description else f"{title}\n{description}"
    if not text.strip():
        return None
    return _INTERN.search(text) is not None


def detect_cs_relevance(title: str) -> CsRelevance:
    folded = title.casefold()
    for keyword in _CS_KEYWORDS:
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", folded):
            return CsRelevance.relevant
    return CsRelevance.unknown


def best_location_class(locations: tuple[str, ...] | list[str]) -> LocationClass:
    if not locations:
        return LocationClass.unknown
    return max((_classify_location(item) for item in locations), key=_LOCATION_RANK.__getitem__)


def location_rank(location_class: LocationClass) -> int:
    return _LOCATION_RANK[location_class]


def _classify_location(value: str) -> LocationClass:
    text = _clean_location(value).casefold()
    remote = re.search(r"\bremote\b", text) is not None
    non_us = any(re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", text) for name in _NON_US)
    us = _mentions_us(text)
    if any(phrase in text for phrase in _DMV_PHRASES):
        return LocationClass.dmv
    if remote and non_us and not us:
        return LocationClass.non_us
    if remote and us:
        return LocationClass.us_remote
    if remote:
        return LocationClass.unknown
    if non_us and not us:
        return LocationClass.non_us
    if us or _mentions_us_state(text):
        return LocationClass.us_other
    return LocationClass.unknown


def _mentions_us(text: str) -> bool:
    return re.search(r"\b(united states|u\.s\.a\.|u\.s\.|usa|us)\b", text) is not None


def _mentions_us_state(text: str) -> bool:
    tokens = set(re.findall(r"[a-z]+", text))
    if tokens & _US_STATES:
        return True
    return any(name in text for name in _US_STATES if " " in name)


def _canonical_term(value: str | None) -> str | None:
    if not value:
        return None
    match = _TERM.search(value)
    if match is None:
        return None
    return f"{match.group(1).casefold().capitalize()} {match.group(2)}"


def _plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = _WHITESPACE.sub(" ", _TAG.sub(" ", value)).strip()
    return text or None


def _clean_location(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()
