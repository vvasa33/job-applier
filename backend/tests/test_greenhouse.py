import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from jobhunter.db.models import Job
from jobhunter.domain.enums import LocationClass
from jobhunter.ingestion.ports import JobSource
from jobhunter.ingestion.raw import RawJob
from jobhunter.ingestion.registry import SourceRegistry
from jobhunter.ingestion.service import IngestionService
from jobhunter.ingestion.sources.greenhouse import USER_AGENT, GreenhouseBoardSource, GreenhouseRequestError

FIXTURES = Path(__file__).parent / "fixtures" / "greenhouse"
APPLY_URL = "https://example.com/jobs/search?gh_jid=100"
REMOTE_URL = "https://boards.greenhouse.io/example/jobs/200?gh_src=abc"
NEXT = '<https://boards-api.greenhouse.io/v1/boards/example/jobs?page=2>; rel="next"'


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["user-agent"] == USER_AGENT
    assert "cookie" not in {key.lower() for key in request.headers}
    assert "sec-ch-ua" not in {key.lower() for key in request.headers}
    path = request.url.path
    if path.endswith("/jobs/100"):
        return httpx.Response(200, json=_load("detail_hybrid.json"))
    if path.endswith("/jobs/200"):
        return httpx.Response(200, json=_load("detail_remote.json"))
    if path.endswith("/jobs/300"):
        return httpx.Response(200, json=_load("detail_onsite.json"))
    if path.endswith("/jobs/400"):
        return httpx.Response(200, json=_load("detail_sparse.json"))
    if path.endswith("/jobs") and request.url.params.get("page") == "2":
        return httpx.Response(200, json=_load("jobs_page_2.json"))
    if path.endswith("/jobs"):
        return httpx.Response(200, json=_load("jobs_page_1.json"), headers={"Link": NEXT})
    return httpx.Response(404, json={"error": "missing"})


def _source(**overrides) -> GreenhouseBoardSource:
    client = overrides.pop("client", None) or httpx.Client(transport=httpx.MockTransport(_handler))
    defaults = {
        "board_token": "example",
        "company": "Example",
        "max_pages": 5,
        "min_interval_s": 0,
        "client": client,
        "sleep": lambda _delay: None,
    }
    defaults.update(overrides)
    return GreenhouseBoardSource(**defaults)


def test_greenhouse_source_matches_the_job_source_protocol() -> None:
    assert isinstance(_source(), JobSource)


def test_discover_paginates_and_skips_duplicates_and_malformed_rows() -> None:
    calls = {"lists": 0}

    def counting(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs") and not request.url.path.endswith("/jobs/100"):
            calls["lists"] += 1
        return _handler(request)

    jobs = list(_source(client=httpx.Client(transport=httpx.MockTransport(counting))).discover())

    assert calls["lists"] == 2
    assert [job["external_id"] for job in jobs] == ["100", "200", "300", "400"]
    assert all(job["title"] != "Account Executive" for job in jobs)
    hybrid = jobs[0]
    assert hybrid["url"] == APPLY_URL
    assert hybrid["requisition_id"] == "R-100"
    assert hybrid["locations"] == ["McLean, VA"]
    assert hybrid["posted_at"] == datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    remote = jobs[1]
    assert remote["url"] == REMOTE_URL
    assert remote["requisition_id"] is None
    assert remote["locations"] == ["Remote - United States"]
    sparse = jobs[3]
    assert sparse["locations"] == []
    assert sparse["description"] is None
    assert sparse["requisition_id"] is None
    assert sparse["posted_at"] is None
    assert sparse["url"] == "https://example.com/jobs/400"


def test_list_without_a_next_link_is_one_page() -> None:
    calls = {"lists": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["lists"] += 1
        return httpx.Response(200, json={"jobs": _load("jobs_page_1.json")["jobs"][:1], "meta": {"total": 50}})

    jobs = list(_source(client=httpx.Client(transport=httpx.MockTransport(handler))).discover())
    assert calls["lists"] == 1
    assert [job["external_id"] for job in jobs] == ["100"]


def test_fetch_detail_preserves_apply_url_id_and_raw_payload() -> None:
    source = _source()
    listing = next(iter(source.discover()))
    detailed = source.fetch_detail(RawJob.model_validate(listing))

    assert detailed is not None
    assert detailed["url"] == APPLY_URL
    assert detailed["external_id"] == "100"
    assert detailed["requisition_id"] == "R-100"
    assert detailed["locations"] == ["Hybrid - McLean, VA", "McLean, VA", "Arlington, VA"]
    assert detailed["description"] == "<p>Build software in McLean.</p>"
    assert detailed["raw"]["detail"]["offices"][1]["location"] == "Arlington, VA"
    assert "absolute_url" not in Job.__table__.columns
    assert "offices" not in Job.__table__.columns


def test_ingestion_stores_greenhouse_jobs_without_greenhouse_columns(db_session) -> None:
    registry = SourceRegistry()
    registry.register(_source())
    report = IngestionService(registry).ingest(db_session, "greenhouse:example")
    db_session.commit()

    assert report.rejected == 0
    assert report.created == 4
    jobs = list(db_session.scalars(select(Job)))
    by_external = {job.sources[0].external_id: job for job in jobs}
    hybrid = by_external["100"]
    assert hybrid.apply_url == APPLY_URL
    assert hybrid.requisition_id == "R-100"
    assert hybrid.location_class == LocationClass.dmv
    assert hybrid.description_text == "Build software in McLean."
    assert hybrid.sources[0].raw["detail"]["id"] == 100
    remote = by_external["200"]
    assert remote.apply_url == REMOTE_URL
    assert remote.requisition_id is None
    assert remote.location_class == LocationClass.us_remote
    assert "Remote - United States" in remote.locations
    assert by_external["300"].location_class == LocationClass.us_other
    assert by_external["400"].location_class == LocationClass.unknown
    assert by_external["400"].locations == []


def test_transient_failure_is_retried() -> None:
    calls = {"jobs": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["jobs"] += 1
        if calls["jobs"] == 1:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})

    slept: list[float] = []
    source = _source(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append)
    assert list(source.discover()) == []
    assert calls["jobs"] == 2
    assert slept == [0.5]


def test_access_refusal_is_not_retried() -> None:
    calls = {"jobs": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["jobs"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    source = _source(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(GreenhouseRequestError, match="refused"):
        list(source.discover())
    assert calls["jobs"] == 1


def test_requests_are_paced() -> None:
    slept: list[float] = []
    now = {"t": 100.0}

    def sleep(delay: float) -> None:
        slept.append(delay)
        now["t"] += delay

    source = _source(min_interval_s=1.0, sleep=sleep, monotonic=lambda: now["t"])
    list(source.discover())
    assert slept
    assert slept[0] == pytest.approx(1.0)


@pytest.mark.live
def test_public_greenhouse_board_returns_internship_postings() -> None:
    with GreenhouseBoardSource(board_token="stripe", company="Stripe", max_pages=1, min_interval_s=1.0) as source:
        found = list(source.discover())
        assert found
        listing = RawJob.model_validate(found[0])
        assert listing.url.startswith("https://")
        assert listing.external_id
        detailed = source.fetch_detail(listing)
    assert detailed is not None
    parsed = RawJob.model_validate(detailed)
    assert parsed.url.startswith("https://")
    assert parsed.external_id == listing.external_id
    assert parsed.raw["detail"]["absolute_url"] == parsed.url
