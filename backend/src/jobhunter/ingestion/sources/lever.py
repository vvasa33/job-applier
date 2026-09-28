"""Public Lever postings source.

Lever publishes an unauthenticated postings API:

    GET https://api.lever.co/v0/postings/{site}?mode=json&skip={n}&limit={n}
    GET https://api.lever.co/v0/postings/{site}/{id}?mode=json

Pages are requested until a page is empty or shorter than the requested limit.
The adapter does not solve challenges, imitate a browser, or retry after an
access refusal. It does not submit applications.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from jobhunter.ingestion.ports import SourceIdentity
from jobhunter.ingestion.raw import RawJob

USER_AGENT = "jobhunter/0.1 (personal local job search)"
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_PLACEHOLDER_LOCATION = re.compile(r"\d+\s+locations?\Z", re.IGNORECASE)
_COUNTRY_NAMES = {
    "US": "United States",
    "USA": "United States",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "IN": "India",
    "IE": "Ireland",
    "AU": "Australia",
    "MX": "Mexico",
    "SG": "Singapore",
}
_REMOTE_COUNTRIES = (
    "united states",
    "united kingdom",
    "canada",
    "germany",
    "france",
    "india",
    "ireland",
    "australia",
    "mexico",
    "singapore",
)


class LeverRequestError(Exception):
    """The public Lever postings API failed, or it refused the request."""


class LeverPostingSource:
    def __init__(
        self,
        *,
        site: str,
        company: str,
        search_terms: tuple[str, ...] = ("intern", "co-op"),
        api_host: str = "api.lever.co",
        page_size: int = 20,
        max_pages: int = 50,
        min_interval_s: float = 1.0,
        max_attempts: int = 3,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.site = _path_segment(site, "site")
        self.api_host = _hostname(api_host)
        self.company = company.strip()
        if not self.company:
            raise ValueError("company is required")
        if page_size < 1 or page_size > 100 or max_pages < 1 or max_attempts < 1:
            raise ValueError("page_size must be 1-100, and max_pages and max_attempts must be positive")
        if min_interval_s < 0:
            raise ValueError("min_interval_s cannot be negative")
        self.search_terms = search_terms
        self.page_size = page_size
        self.max_pages = max_pages
        self.min_interval_s = min_interval_s
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._monotonic = monotonic
        self._next_request_at = 0.0
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30.0),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> LeverPostingSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def identify(self) -> SourceIdentity:
        return SourceIdentity(
            key=f"lever:{self.site}",
            ats_type="lever",
            board_key=self.site,
            label=self.company,
            company=self.company,
        )

    def discover(self) -> Iterable[Mapping[str, Any]]:
        seen_ids: set[str] = set()
        skip = 0
        for _page in range(self.max_pages):
            body = self._request(self._list_url(skip))
            if isinstance(body, dict):
                raise LeverRequestError("Lever job list was not a JSON array")
            if not isinstance(body, list) or not body:
                return
            for posting in body:
                raw = _posting_to_raw(posting, self.company, self._hosted_origin())
                if raw is None:
                    continue
                commitment = ""
                if isinstance(posting, dict):
                    categories = posting.get("categories")
                    if isinstance(categories, dict):
                        commitment = _clean(categories.get("commitment"))
                if not _matches(raw["title"], commitment, self.search_terms):
                    continue
                if raw["external_id"] in seen_ids:
                    continue
                seen_ids.add(raw["external_id"])
                yield raw
            if len(body) < self.page_size:
                return
            skip += self.page_size

    def fetch_detail(self, job: RawJob) -> Mapping[str, Any] | None:
        posting_id = str(job.raw.get("id") or job.external_id)
        if not posting_id or "/" in posting_id:
            return None
        body = self._request(self._detail_url(posting_id), allow_not_found=True)
        if body is None:
            return None
        if not isinstance(body, dict):
            raise LeverRequestError("Lever job detail was not a JSON object")
        listing = job.raw.get("listing") if isinstance(job.raw.get("listing"), dict) else {}
        detailed = _posting_to_raw(body, self.company, self._hosted_origin())
        if detailed is None:
            return None
        detailed["url"] = detailed["url"] or job.url
        detailed["raw"] = {"id": detailed["external_id"], "listing": listing, "detail": body}
        return detailed

    def _request(self, url: str, *, allow_not_found: bool = False) -> Any:
        delay = 0.5
        last_status: int | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            try:
                response = self._client.request(
                    "GET",
                    url,
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                )
            except httpx.TransportError as exc:
                if attempt == self.max_attempts:
                    raise LeverRequestError(f"Lever request failed: {exc.__class__.__name__}") from exc
                self._sleep(delay)
                delay *= 2
                continue
            last_status = response.status_code
            if response.status_code == 404 and allow_not_found:
                return None
            if response.status_code in {401, 403}:
                raise LeverRequestError(
                    f"Lever refused the request with HTTP {response.status_code}. Not retrying."
                )
            if response.status_code in _TRANSIENT_STATUS:
                if attempt == self.max_attempts:
                    break
                self._sleep(_retry_after(response, delay))
                delay *= 2
                continue
            if response.status_code >= 400:
                raise LeverRequestError(f"Lever returned HTTP {response.status_code}")
            if "json" not in response.headers.get("content-type", "").lower():
                raise LeverRequestError("Lever did not return JSON")
            return response.json()
        raise LeverRequestError(f"Lever request failed with HTTP {last_status}")

    def _pace(self) -> None:
        if self.min_interval_s == 0:
            return
        delay = self._next_request_at - self._monotonic()
        if delay > 0:
            self._sleep(delay)
        self._next_request_at = self._monotonic() + self.min_interval_s

    def _list_url(self, skip: int) -> str:
        query = urlencode({"mode": "json", "skip": skip, "limit": self.page_size})
        return f"https://{self.api_host}/v0/postings/{self.site}?{query}"

    def _detail_url(self, posting_id: str) -> str:
        return f"https://{self.api_host}/v0/postings/{self.site}/{posting_id}?mode=json"

    def _hosted_origin(self) -> str:
        if self.api_host.startswith("api.eu."):
            return f"https://jobs.eu.lever.co/{self.site}"
        return f"https://jobs.lever.co/{self.site}"


def _posting_to_raw(posting: object, company: str, hosted_origin: str) -> dict[str, Any] | None:
    if not isinstance(posting, dict):
        return None
    external_id = _clean(posting.get("id"))
    title = _clean(posting.get("text"))
    apply_url = _clean(posting.get("applyUrl"))
    hosted_url = _clean(posting.get("hostedUrl"))
    url = _absolute_url(apply_url) or _absolute_url(hosted_url)
    if not url and not apply_url and not hosted_url and external_id:
        url = f"{hosted_origin}/{external_id}/apply"
    if not external_id or not title or not url:
        return None
    description = _clean(posting.get("description")) or _clean(posting.get("descriptionPlain")) or None
    return {
        "external_id": external_id,
        "title": title,
        "url": url,
        "company": company,
        "locations": _locations(posting),
        "description": description,
        "requisition_id": None,
        "posted_at": _posted_at(posting.get("createdAt")),
        "raw": {"id": external_id, "listing": posting},
    }


def _locations(posting: Mapping[str, Any]) -> list[str]:
    categories = posting.get("categories") if isinstance(posting.get("categories"), dict) else {}
    places: list[str] = []
    primary = _clean(categories.get("location"))
    if primary and not _is_placeholder(primary):
        places.append(primary)
    extras = categories.get("allLocations")
    if isinstance(extras, list):
        for item in extras:
            text = _clean(item)
            if text and not _is_placeholder(text):
                places.append(text)
    country = _COUNTRY_NAMES.get(_clean(posting.get("country")).upper())
    if country:
        places.append(country)
    return _with_workplace(places, posting.get("workplaceType"))


def _with_workplace(places: list[str], workplace: object) -> list[str]:
    kind = _clean(workplace).casefold()
    ordered = [place for place in _dedupe(places) if place.casefold() not in {"hybrid", "remote"}]
    if "hybrid" in kind:
        if ordered:
            return _dedupe([f"Hybrid - {ordered[0]}", *ordered])
        return ["Hybrid"]
    if "remote" in kind:
        return _dedupe([*ordered, _remote_label(ordered)])
    return _dedupe(places)


def _remote_label(places: list[str]) -> str:
    for place in places:
        folded = place.casefold()
        if re.search(r"\b(united states|u\.s\.a\.|u\.s\.|usa|us)\b", folded):
            return "Remote - United States"
        for name in _REMOTE_COUNTRIES:
            if name != "united states" and re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", folded):
                return f"Remote - {name.title()}"
    return "Remote"


def _posted_at(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if seconds > 10_000_000_000:
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _matches(title: str, extra: str, terms: tuple[str, ...]) -> bool:
    if not terms:
        return True
    haystack = f"{title}\n{extra}"
    for term in terms:
        folded = term.strip().casefold()
        if not folded:
            continue
        if folded in {"co-op", "coop", "co op"}:
            if re.search(r"(?<![a-z0-9])co[-\s]?ops?(?![a-z0-9])", haystack, re.IGNORECASE):
                return True
            continue
        if folded == "intern":
            if re.search(r"(?<![a-z0-9])intern(?:ship)?s?(?![a-z0-9])", haystack, re.IGNORECASE):
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])", haystack, re.IGNORECASE):
            return True
    return False


def _absolute_url(value: object) -> str:
    text = _clean(value)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return ""


def _is_placeholder(value: str) -> bool:
    return _PLACEHOLDER_LOCATION.fullmatch(value.strip()) is not None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _retry_after(response: httpx.Response, fallback: float) -> float:
    header = response.headers.get("Retry-After", "").strip()
    if header.isdigit():
        return float(header)
    return fallback


def _hostname(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path).strip().lower()
    if not host or "/" in host or " " in host:
        raise ValueError("api_host must be a hostname")
    return host


def _path_segment(value: str, label: str) -> str:
    text = value.strip().strip("/")
    if not text or "/" in text:
        raise ValueError(f"{label} must be a single path segment")
    return text
