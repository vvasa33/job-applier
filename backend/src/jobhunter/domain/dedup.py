"""Deterministic duplicate detection for canonical jobs.

Signals are applied in order. The first match wins:

1. Same source board and external id.
2. Same canonical application URL.
3. Same company, normalized title, and normalized location.
4. Same company and description hash, when the titles and locations do not contradict.
5. Local text similarity, and only for pairs that the earlier signals did not decide
   and that already share a company and a compatible location.

Nothing in this module calls a model or an embedding service. Semantic matches are
not transitive: a chain of near-matches does not collapse into one job.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from jobhunter.domain.identity import canonical_url, normalize_name

_TAG = re.compile(r"<[^>]+>")
_PLACEHOLDER_LOCATION = re.compile(r"\d+\s+locations?\Z", re.IGNORECASE)
_MIN_DESCRIPTION = 80
_TITLE_JACCARD = 0.66
_DESCRIPTION_JACCARD = 0.9
_TITLE_RATIO = 0.92
_FILLER = frozenset({"a", "an", "and", "area", "metro", "of", "office", "offices", "region", "the"})
_COUNTRY = frozenset({
    "australia",
    "britain",
    "canada",
    "france",
    "germany",
    "india",
    "ireland",
    "kingdom",
    "mexico",
    "singapore",
    "states",
    "united",
})
_STATES = {
    "al": "alabama",
    "ak": "alaska",
    "az": "arizona",
    "ar": "arkansas",
    "ca": "california",
    "co": "colorado",
    "ct": "connecticut",
    "de": "delaware",
    "dc": "district columbia",
    "fl": "florida",
    "ga": "georgia",
    "hi": "hawaii",
    "id": "idaho",
    "il": "illinois",
    "in": "indiana",
    "ia": "iowa",
    "ks": "kansas",
    "ky": "kentucky",
    "la": "louisiana",
    "me": "maine",
    "md": "maryland",
    "ma": "massachusetts",
    "mi": "michigan",
    "mn": "minnesota",
    "ms": "mississippi",
    "mo": "missouri",
    "mt": "montana",
    "ne": "nebraska",
    "nv": "nevada",
    "nh": "new hampshire",
    "nj": "new jersey",
    "nm": "new mexico",
    "ny": "new york",
    "nc": "north carolina",
    "nd": "north dakota",
    "oh": "ohio",
    "ok": "oklahoma",
    "or": "oregon",
    "pa": "pennsylvania",
    "ri": "rhode island",
    "sc": "south carolina",
    "sd": "south dakota",
    "tn": "tennessee",
    "tx": "texas",
    "ut": "utah",
    "vt": "vermont",
    "va": "virginia",
    "wa": "washington",
    "wv": "west virginia",
    "wi": "wisconsin",
    "wy": "wyoming",
}
_STATE_NAMES = frozenset(_STATES.values())
_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=timezone.utc)


class DedupSignal(StrEnum):
    source_external_id = "source_external_id"
    canonical_url = "canonical_url"
    title_location = "title_location"
    content_hash = "content_hash"
    semantic = "semantic"


_SIGNAL_RANK = {signal: index for index, signal in enumerate(DedupSignal)}


@dataclass(frozen=True)
class SourceRef:
    ats_type: str
    board_key: str
    external_id: str
    url: str
    first_seen_at: datetime | None = None


@dataclass(frozen=True)
class JobProfile:
    company: str
    title: str
    locations: tuple[str, ...] = ()
    description_text: str | None = None
    sources: tuple[SourceRef, ...] = ()
    apply_url: str | None = None


@dataclass(frozen=True)
class DuplicateMatch:
    signal: DedupSignal


def match_jobs(left: JobProfile, right: JobProfile) -> DuplicateMatch | None:
    """Return the strongest signal that says these two profiles are the same job."""
    if _source_keys(left) & _source_keys(right):
        return DuplicateMatch(DedupSignal.source_external_id)
    if _canonical_urls(left) & _canonical_urls(right):
        return DuplicateMatch(DedupSignal.canonical_url)
    same_company = _company_key(left.company) == _company_key(right.company) and bool(_company_key(left.company))
    same_title = title_key(left.title) == title_key(right.title) and bool(title_key(left.title))
    left_locations = location_keys(left.locations)
    right_locations = location_keys(right.locations)
    if same_company and same_title and location_overlap(left_locations, right_locations):
        return DuplicateMatch(DedupSignal.title_location)
    if (
        same_company
        and _hashes_match(left.description_text, right.description_text)
        and locations_compatible(left_locations, right_locations)
        and titles_compatible(left.title, right.title)
    ):
        return DuplicateMatch(DedupSignal.content_hash)
    if _semantic_match(
        left,
        right,
        same_company=same_company,
        same_title=same_title,
        left_locations=left_locations,
        right_locations=right_locations,
    ):
        return DuplicateMatch(DedupSignal.semantic)
    return None


def choose_preferred_url(sources: tuple[SourceRef, ...] | list[SourceRef]) -> str:
    """Pick one apply URL. Earliest sighting wins, then https, then source identity, then the URL text."""
    if not sources:
        raise ValueError("at least one source URL is required")
    chosen = min(sources, key=_url_sort_key)
    return chosen.url


def title_key(title: str) -> str:
    # Protect "co-op" before company-suffix cleanup, which would otherwise delete the token "co".
    text = re.sub(r"co[\s-]?ops?", " coop ", title, flags=re.IGNORECASE)
    tokens: list[str] = []
    for token in normalize_name(text).split():
        if token in {"internship", "internships"}:
            tokens.append("intern")
        elif token == "engineering":
            tokens.append("engineer")
        elif token == "swe":
            tokens.extend(("software", "engineer"))
        elif token == "coop":
            tokens.extend(("co", "op"))
        elif re.fullmatch(r"(summer|fall|spring|winter)", token):
            continue
        elif re.fullmatch(r"20\d{2}", token):
            continue
        else:
            tokens.append(token)
    return " ".join(sorted(set(tokens)))


def location_keys(locations: tuple[str, ...] | list[str]) -> frozenset[str]:
    keys: set[str] = set()
    for location in locations:
        keys.update(_keys_for_place(location))
    return frozenset(keys)


def location_overlap(left: frozenset[str], right: frozenset[str]) -> bool:
    if left & right:
        return True
    return bool(_match_families(left) & _match_families(right))


def locations_compatible(left: frozenset[str], right: frozenset[str]) -> bool:
    if not left or not right:
        return True
    if location_overlap(left, right):
        return True
    return _state_covers(left, right) or _state_covers(right, left)


def titles_compatible(left: str, right: str) -> bool:
    if _intern_conflict(left, right):
        return False
    left_key = title_key(left)
    right_key = title_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    return _jaccard(left_key, right_key) >= _TITLE_JACCARD


def content_hash(description: str | None) -> str | None:
    text = normalize_description(description)
    if text is None or len(text) < _MIN_DESCRIPTION:
        return None
    return hashlib.sha256(text.encode()).hexdigest()


def normalize_description(description: str | None) -> str | None:
    if description is None:
        return None
    text = html.unescape(description)
    text = _TAG.sub(" ", text)
    text = normalize_name(text)
    return text or None


def stronger_signal(current: DedupSignal | None, candidate: DedupSignal) -> DedupSignal:
    if current is None or _SIGNAL_RANK[candidate] < _SIGNAL_RANK[current]:
        return candidate
    return current


def _semantic_match(
    left: JobProfile,
    right: JobProfile,
    *,
    same_company: bool,
    same_title: bool,
    left_locations: frozenset[str],
    right_locations: frozenset[str],
) -> bool:
    if not same_company or same_title or _intern_conflict(left.title, right.title):
        return False
    if not locations_compatible(left_locations, right_locations):
        return False
    from difflib import SequenceMatcher

    if SequenceMatcher(None, title_key(left.title), title_key(right.title)).ratio() >= _TITLE_RATIO:
        return True
    left_description = normalize_description(left.description_text)
    right_description = normalize_description(right.description_text)
    if (
        left_description is None
        or right_description is None
        or len(left_description) < _MIN_DESCRIPTION
        or len(right_description) < _MIN_DESCRIPTION
    ):
        return False
    if _jaccard(title_key(left.title), title_key(right.title)) < _TITLE_JACCARD:
        return False
    return _jaccard(left_description, right_description) >= _DESCRIPTION_JACCARD


def _source_keys(profile: JobProfile) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for source in profile.sources:
        ats = source.ats_type.strip().casefold()
        board = source.board_key.strip().casefold()
        external = source.external_id.strip().casefold()
        if ats and board and external:
            keys.add((ats, board, external))
    return keys


def _canonical_urls(profile: JobProfile) -> set[str]:
    urls = {source.url for source in profile.sources}
    if profile.apply_url:
        urls.add(profile.apply_url)
    return {canonical_url(url) for url in urls if url and url.strip()}


def _company_key(company: str) -> str:
    return normalize_name(company)


def _hashes_match(left: str | None, right: str | None) -> bool:
    left_hash = content_hash(left)
    right_hash = content_hash(right)
    return left_hash is not None and left_hash == right_hash


def _intern_conflict(left: str, right: str) -> bool:
    return _internish(left) != _internish(right)


def _internish(title: str) -> bool:
    tokens = set(title_key(title).split())
    return "intern" in tokens or {"co", "op"} <= tokens


def _keys_for_place(location: str) -> set[str]:
    if _PLACEHOLDER_LOCATION.fullmatch(location.strip()):
        return set()
    tokens = _location_tokens(location)
    if not tokens:
        return set()
    remote = "remote" in tokens
    body = [token for token in tokens if token not in {"hybrid", "remote"}]
    united_states = "united" in body and "states" in body
    city = [token for token in body if token not in _COUNTRY]
    keys: set[str] = set()
    if city:
        keys.add(" ".join(sorted(set(city))))
    if remote and united_states:
        keys.add("remote states united")
    elif remote and not city:
        keys.add(" ".join(sorted({"remote", *body})) if body else "remote")
    elif remote:
        keys.add("remote")
    return keys


def _location_tokens(location: str) -> list[str]:
    text = location.casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens: list[str] = []
    for token in text.split():
        if token in _FILLER or token == "hybrid":
            continue
        expanded = _STATES.get(token)
        if expanded:
            tokens.extend(expanded.split())
        elif token in {"usa", "us", "u"}:
            tokens.extend(("united", "states"))
        elif token == "nyc":
            tokens.extend(("new", "york"))
        elif token == "uk":
            tokens.extend(("united", "kingdom"))
        else:
            tokens.append(token)
    return tokens


def _match_families(keys: frozenset[str]) -> set[str]:
    families: set[str] = set()
    if "remote" in keys or "remote states united" in keys:
        families.add("remote-us")
    return families


def _state_covers(broader: frozenset[str], specific: frozenset[str]) -> bool:
    for key in broader:
        if key not in _STATE_NAMES:
            continue
        tokens = set(key.split())
        if any(tokens <= set(other.split()) for other in specific):
            return True
    return False


def _jaccard(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _url_sort_key(source: SourceRef) -> tuple:
    seen = source.first_seen_at or _FAR_FUTURE
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    scheme = 0 if source.url.lower().startswith("https://") else 1
    return (
        seen,
        scheme,
        source.ats_type.casefold(),
        source.board_key.casefold(),
        source.external_id.casefold(),
        canonical_url(source.url),
        source.url,
    )
