"""Public Greenhouse job-board source.

The documented board API is unauthenticated:

    GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
    GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{id}

The list endpoint returns the whole board in one response and ignores a page
query. This adapter still follows an RFC 5988 ``Link: rel="next"`` header when
a response includes one, and it will not invent page numbers. It does not
solve challenges, imitate a browser, or retry after an access refusal.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from jobhunter.ingestion.ports import SourceIdentity
from jobhunter.ingestion.raw import RawJob

USER_AGENT = "jobhunter/0.1 (personal local job search)"
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_PLACEHOLDER_LOCATION = re.compile(r"\d+\s+locations?\Z", re.IGNORECASE)
_PLACEHOLDER_REQUISITION = frozenset({
    "see opening id",
    "n/a",
    "na",
    "none",
    "null",
    "tbd",
    "-",
    "not available",
})
_NEXT_LINK = re.compile(r"<([^>]+)>\s*;\s*([^,]+)", re.IGNORECASE)
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


class GreenhouseRequestError(Exception):
    """The public Greenhouse board failed, or it refused the request."""


class GreenhouseBoardSource:
    def __init__(
        self,
        *,
        board_token: str,
        company: str,
        search_terms: tuple[str, ...] = ("intern", "co-op"),
        api_host: str = "boards-api.greenhouse.io",
        max_pages: int = 20,
        min_interval_s: float = 1.0,
        max_attempts: int = 3,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.board_token = _path_segment(board_token, "board_token")
        self.api_host = _hostname(api_host)
        self.company = company.strip()
        if not self.company:
            raise ValueError("company is required")
        if max_pages < 1 or max_attempts < 1:
            raise ValueError("max_pages and max_attempts must be positive")
        if min_interval_s < 0:
            raise ValueError("min_interval_s cannot be negative")
        self.search_terms = search_terms
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

    def __enter__(self) -> GreenhouseBoardSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def identify(self) -> SourceIdentity:
        return SourceIdentity(
            key=f"greenhouse:{self.board_token}",
            ats_type="greenhouse",
            board_key=self.board_token,
            label=self.company,
            company=self.company,
        )

    def discover(self) -> Iterable[Mapping[str, Any]]:
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        url: str | None = self._jobs_url()
        pages = 0
        while url and pages < self.max_pages and url not in seen_urls:
            seen_urls.add(url)
            body, next_url = self._request(url)
            pages += 1
            if body is None:
                raise GreenhouseRequestError("Greenhouse job list returned no JSON body")
            jobs = body.get("jobs")
            if not isinstance(jobs, list) or not jobs:
                return
            for job in jobs:
                raw = _listing_to_raw(job, self.company)
                if raw is None or not _matches(raw["title"], "", self.search_terms):
                    continue
                external_id = raw["external_id"]
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)
                yield raw
            url = next_url

    def fetch_detail(self, job: RawJob) -> Mapping[str, Any] | None:
        posting_id = str(job.raw.get("id") or job.external_id)
        if not posting_id or "/" in posting_id:
            return None
        body, _next = self._request(self._detail_url(posting_id), allow_not_found=True)
        if body is None:
            return None
        listing = job.raw.get("listing") if isinstance(job.raw.get("listing"), dict) else {}
        return _detail_to_raw(body, listing=listing, company=self.company, fallback_url=job.url)

    def _request(self, url: str, *, allow_not_found: bool = False) -> tuple[dict[str, Any] | None, str | None]:
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
                    raise GreenhouseRequestError(
                        f"Greenhouse request failed: {exc.__class__.__name__}"
                    ) from exc
                self._sleep(delay)
                delay *= 2
                continue
            last_status = response.status_code
            if response.status_code == 404 and allow_not_found:
                return None, None
            if response.status_code in {401, 403}:
                raise GreenhouseRequestError(
                    f"Greenhouse refused the request with HTTP {response.status_code}. Not retrying."
                )
            if response.status_code in _TRANSIENT_STATUS:
                if attempt == self.max_attempts:
                    break
                self._sleep(_retry_after(response, delay))
                delay *= 2
                continue
            if response.status_code >= 400:
                raise GreenhouseRequestError(f"Greenhouse returned HTTP {response.status_code}")
            if "json" not in response.headers.get("content-type", "").lower():
                raise GreenhouseRequestError("Greenhouse did not return JSON")
            payload = response.json()
            if not isinstance(payload, dict):
                raise GreenhouseRequestError("Greenhouse returned a JSON value that is not an object")
            return payload, _next_url(response.headers.get("link", ""), url, self.api_host, self.board_token)
        raise GreenhouseRequestError(f"Greenhouse request failed with HTTP {last_status}")

    def _pace(self) -> None:
        if self.min_interval_s == 0:
            return
        delay = self._next_request_at - self._monotonic()
        if delay > 0:
            self._sleep(delay)
        self._next_request_at = self._monotonic() + self.min_interval_s

    def _jobs_url(self) -> str:
        return f"https://{self.api_host}/v1/boards/{self.board_token}/jobs"

    def _detail_url(self, posting_id: str) -> str:
        return f"https://{self.api_host}/v1/boards/{self.board_token}/jobs/{posting_id}"


def _listing_to_raw(job: object, company: str) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return None
    external_id = _identifier(job.get("id"))
    title = _clean(job.get("title"))
    url = _absolute_url(job.get("absolute_url"))
    if not external_id or not title or not url:
        return None
    return {
        "external_id": external_id,
        "title": title,
        "url": url,
        "company": company,
        "locations": _locations(job, explicit=None),
        "description": _description(job.get("content")),
        "requisition_id": _requisition(job.get("requisition_id")),
        "posted_at": _posted_at(job.get("first_published") or job.get("updated_at")),
        "raw": {"id": external_id, "listing": job},
    }


def _detail_to_raw(
    info: dict[str, Any],
    *,
    listing: dict[str, Any],
    company: str,
    fallback_url: str,
) -> dict[str, Any]:
    external_id = _identifier(info.get("id")) or _identifier(listing.get("id")) or ""
    title = _clean(info.get("title")) or _clean(listing.get("title"))
    url = _absolute_url(info.get("absolute_url")) or fallback_url
    return {
        "external_id": external_id,
        "title": title,
        "url": url,
        "company": company,
        "locations": _locations(info, explicit=None) or _locations(listing, explicit=None),
        "description": _description(info.get("content")) or _description(listing.get("content")),
        "requisition_id": _requisition(info.get("requisition_id")) or _requisition(listing.get("requisition_id")),
        "posted_at": _posted_at(info.get("first_published") or info.get("updated_at") or listing.get("first_published")),
        "raw": {"id": external_id, "listing": listing, "detail": info},
    }


def _locations(payload: Mapping[str, Any], explicit: str | None) -> list[str]:
    places: list[str] = []
    location = payload.get("location")
    if isinstance(location, dict):
        text = _clean(location.get("name"))
        if text and not _is_placeholder(text):
            places.append(text)
    elif isinstance(location, str) and _clean(location) and not _is_placeholder(location):
        places.append(_clean(location))
    offices = payload.get("offices")
    if isinstance(offices, list):
        for office in offices:
            if not isinstance(office, dict):
                continue
            text = _clean(office.get("location")) or _clean(office.get("name"))
            if text and not _is_placeholder(text):
                places.append(text)
    return _with_workplace(places, explicit)


def _with_workplace(places: list[str], explicit: str | None) -> list[str]:
    kind = _workplace_kind(places, explicit)
    ordered = [place for place in _dedupe(places) if place.casefold() not in {"hybrid", "remote"}]
    if kind == "hybrid":
        if ordered:
            return _dedupe([f"Hybrid - {ordered[0]}", *ordered])
        return ["Hybrid"]
    if kind == "remote":
        return _dedupe([*ordered, _remote_label(ordered)])
    return _dedupe(places)


def _workplace_kind(places: list[str], explicit: str | None) -> str:
    folded = _clean(explicit).casefold()
    if "hybrid" in folded:
        return "hybrid"
    if "remote" in folded:
        return "remote"
    for place in places:
        if place.casefold() in {"hybrid", "remote"}:
            return place.casefold()
    return ""


def _remote_label(places: list[str]) -> str:
    for place in places:
        folded = place.casefold()
        if re.search(r"\b(united states|u\.s\.a\.|u\.s\.|usa|us)\b", folded):
            return "Remote - United States"
        for name in _REMOTE_COUNTRIES:
            if name != "united states" and re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", folded):
                return f"Remote - {name.title()}"
    return "Remote"


def _description(value: object) -> str | None:
    text = _clean(value)
    if not text:
        return None
    return html.unescape(text)


def _requisition(value: object) -> str | None:
    text = _clean(value)
    if not text or text.casefold() in _PLACEHOLDER_REQUISITION:
        return None
    return text


def _posted_at(value: object) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


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


def _next_url(header: str, current: str, api_host: str, board_token: str) -> str | None:
    for match in _NEXT_LINK.finditer(header):
        attributes = match.group(2)
        if re.search(r"(?:^|;)\s*rel\s*=\s*\"?next\"?", attributes, re.IGNORECASE) is None:
            continue
        url = urljoin(current, match.group(1).strip())
        parsed = urlparse(url)
        prefix = f"/v1/boards/{board_token}/jobs"
        if parsed.scheme == "https" and parsed.netloc.casefold() == api_host and parsed.path.startswith(prefix):
            return url
    return None


def _identifier(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return _clean(value)


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
