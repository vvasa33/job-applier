"""Public Workday career-site source.

Workday career pages load listings from their own JSON endpoint:

    POST https://{host}/wday/cxs/{tenant}/{site}/jobs
    GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}

This adapter calls only that public endpoint, with an identifiable user agent.
It does not solve challenges, imitate a browser, or retry after an access refusal.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from jobhunter.ingestion.ports import SourceIdentity
from jobhunter.ingestion.raw import RawJob

USER_AGENT = "jobhunter/0.1 (personal local job search)"
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_PLACEHOLDER_LOCATION = re.compile(r"\d+\s+locations?\Z", re.IGNORECASE)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class WorkdayRequestError(Exception):
    """The public Workday endpoint failed, or it refused the request."""


class WorkdayCareerSource:
    def __init__(
        self,
        *,
        host: str,
        tenant: str,
        site: str,
        company: str,
        search_terms: tuple[str, ...] = ("intern", "co-op"),
        page_size: int = 20,
        max_pages: int = 50,
        min_interval_s: float = 1.0,
        max_attempts: int = 3,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = _hostname(host)
        self.tenant = _path_segment(tenant, "tenant")
        self.site = _path_segment(site, "site")
        self.company = company.strip()
        if not self.company:
            raise ValueError("company is required")
        if page_size < 1 or max_pages < 1 or max_attempts < 1:
            raise ValueError("page_size, max_pages, and max_attempts must be positive")
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
            timeout=httpx.Timeout(20.0),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> WorkdayCareerSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def identify(self) -> SourceIdentity:
        board = f"{self.tenant}/{self.site}"
        return SourceIdentity(
            key=f"workday:{board}",
            ats_type="workday",
            board_key=board,
            label=self.company,
            company=self.company,
        )

    def discover(self) -> Iterable[Mapping[str, Any]]:
        seen_paths: set[str] = set()
        for term in self.search_terms:
            yield from self._search(term, seen_paths)

    def fetch_detail(self, job: RawJob) -> Mapping[str, Any] | None:
        external_path = str(job.raw.get("externalPath") or "")
        if not external_path:
            return None
        body = self._request("GET", self._detail_url(external_path), allow_not_found=True)
        if body is None:
            return None
        info = body.get("jobPostingInfo")
        if not isinstance(info, dict):
            raise WorkdayRequestError("Workday job detail did not include jobPostingInfo")
        listing = job.raw.get("listing") if isinstance(job.raw.get("listing"), dict) else {}
        return _detail_to_raw(
            info,
            listing=listing,
            external_path=external_path,
            company=self.company,
            fallback_url=job.url,
            detail_body=body,
        )

    def _search(self, term: str, seen_paths: set[str]) -> Iterable[Mapping[str, Any]]:
        offset = 0
        pages = 0
        while pages < self.max_pages:
            body = self._request(
                "POST",
                self._jobs_url(),
                json_body={
                    "appliedFacets": {},
                    "limit": self.page_size,
                    "offset": offset,
                    "searchText": term,
                },
            )
            pages += 1
            if body is None:
                raise WorkdayRequestError("Workday job search returned no JSON body")
            postings = body.get("jobPostings") or []
            if not isinstance(postings, list) or not postings:
                return
            fresh = 0
            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                external_path = str(posting.get("externalPath") or "")
                title = str(posting.get("title") or "").strip()
                if not external_path or not title or external_path in seen_paths:
                    continue
                seen_paths.add(external_path)
                fresh += 1
                yield _listing_to_raw(posting, external_path, self._career_url(external_path), self.company)
            offset += len(postings)
            total = body.get("total")
            if fresh == 0 or (isinstance(total, int) and offset >= total):
                return

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        delay = 0.5
        last_status: int | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            try:
                response = self._client.request(
                    method,
                    url,
                    json=json_body,
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                )
            except httpx.TransportError as exc:
                if attempt == self.max_attempts:
                    raise WorkdayRequestError(f"Workday request failed: {exc.__class__.__name__}") from exc
                self._sleep(delay)
                delay *= 2
                continue
            last_status = response.status_code
            if response.status_code == 404 and allow_not_found:
                return None
            if response.status_code in {401, 403}:
                raise WorkdayRequestError(
                    f"Workday refused the request with HTTP {response.status_code}. Not retrying."
                )
            if response.status_code in _TRANSIENT_STATUS:
                if attempt == self.max_attempts:
                    break
                self._sleep(_retry_after(response, delay))
                delay *= 2
                continue
            if response.status_code >= 400:
                raise WorkdayRequestError(f"Workday returned HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                raise WorkdayRequestError("Workday did not return JSON")
            payload = response.json()
            if not isinstance(payload, dict):
                raise WorkdayRequestError("Workday returned a JSON value that is not an object")
            return payload
        raise WorkdayRequestError(f"Workday request failed with HTTP {last_status}")

    def _pace(self) -> None:
        if self.min_interval_s == 0:
            return
        delay = self._next_request_at - self._monotonic()
        if delay > 0:
            self._sleep(delay)
        self._next_request_at = self._monotonic() + self.min_interval_s

    def _jobs_url(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def _detail_url(self, external_path: str) -> str:
        path = external_path if external_path.startswith("/") else f"/{external_path}"
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}{path}"

    def _career_url(self, external_path: str) -> str:
        path = external_path if external_path.startswith("/") else f"/{external_path}"
        return f"https://{self.host}/{self.site}{path}"


def _listing_to_raw(posting: dict[str, Any], external_path: str, url: str, company: str) -> dict[str, Any]:
    requisition = _requisition_from_bullets(posting.get("bulletFields"))
    return {
        "external_id": external_path,
        "title": str(posting.get("title") or "").strip(),
        "url": url,
        "company": company,
        "locations": _listing_locations(posting),
        "requisition_id": requisition,
        "raw": {"externalPath": external_path, "listing": posting},
    }


def _detail_to_raw(
    info: dict[str, Any],
    *,
    listing: dict[str, Any],
    external_path: str,
    company: str,
    fallback_url: str,
    detail_body: dict[str, Any],
) -> dict[str, Any]:
    external_url = _clean(info.get("externalUrl")) or fallback_url
    posting_id = _clean(info.get("id")) or external_path
    requisition = _clean(info.get("jobReqId")) or _requisition_from_bullets(listing.get("bulletFields"))
    return {
        "external_id": posting_id,
        "title": _clean(info.get("title")) or _clean(listing.get("title")) or "",
        "url": external_url,
        "company": company,
        "locations": _detail_locations(info),
        "description": info.get("jobDescription") if isinstance(info.get("jobDescription"), str) else None,
        "requisition_id": requisition,
        "posted_at": _posted_at(info.get("startDate")),
        "raw": {"externalPath": external_path, "listing": listing, "detail": detail_body},
    }


def _listing_locations(posting: dict[str, Any]) -> list[str]:
    places: list[str] = []
    for part in _split_locations(posting.get("locationsText")):
        if not _is_placeholder(part):
            places.append(part)
    return _with_workplace(places, posting.get("remoteType"), country=None)


def _detail_locations(info: dict[str, Any]) -> list[str]:
    places: list[str] = []
    primary = _clean(info.get("location"))
    if primary and not _is_placeholder(primary):
        places.append(primary)
    extras = info.get("additionalLocations")
    if isinstance(extras, list):
        for item in extras:
            text = _location_text(item)
            if text and not _is_placeholder(text):
                places.append(text)
    return _with_workplace(places, info.get("remoteType"), country=_country(info))


def _with_workplace(places: list[str], remote_type: object, country: str | None) -> list[str]:
    kind = _clean(remote_type).casefold()
    ordered = _dedupe(places)
    if "hybrid" in kind:
        if ordered:
            return _dedupe([f"Hybrid - {ordered[0]}", *ordered])
        return ["Hybrid"]
    if "remote" in kind:
        label = f"Remote - {country}" if country else "Remote"
        return _dedupe([*ordered, label])
    return ordered


def _country(info: dict[str, Any]) -> str | None:
    country = info.get("country")
    if isinstance(country, dict):
        return _clean(country.get("descriptor")) or None
    return _clean(country) or None


def _location_text(item: object) -> str:
    if isinstance(item, str):
        return _clean(item)
    if isinstance(item, dict):
        for key in ("descriptor", "location", "locationsText"):
            text = _clean(item.get(key))
            if text:
                return text
    return ""


def _requisition_from_bullets(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        text = _clean(item)
        if text and any(character.isdigit() for character in text):
            return text
    return None


def _posted_at(value: object) -> datetime | None:
    text = _clean(value)
    if not _DATE.fullmatch(text):
        return None
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _split_locations(value: object) -> list[str]:
    text = _clean(value)
    if not text:
        return []
    return [_clean(part) for part in text.split(";") if _clean(part)]


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
        raise ValueError("host must be a Workday career site hostname")
    return host


def _path_segment(value: str, label: str) -> str:
    text = value.strip().strip("/")
    if not text or "/" in text:
        raise ValueError(f"{label} must be a single path segment")
    return text
