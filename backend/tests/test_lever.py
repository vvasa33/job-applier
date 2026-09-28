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
from jobhunter.ingestion.sources.lever import USER_AGENT, LeverPostingSource, LeverRequestError

FIXTURES = Path(__file__).parent / "fixtures" / "lever"
APPLY_URL = "https://jobs.lever.co/example/posting-100/apply?lever-source=jobhunter"
POSTINGS = json.loads((FIXTURES / "postings.json").read_text(encoding="utf-8"))


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["user-agent"] == USER_AGENT
    assert "cookie" not in {key.lower() for key in request.headers}
    assert "sec-ch-ua" not in {key.lower() for key in request.headers}
    path = request.url.path
    if path == "/v0/postings/example":
        assert request.url.params["mode"] == "json"
        skip = int(request.url.params["skip"])
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json=POSTINGS[skip : skip + limit])
    if path.endswith("/posting-100"):
        return httpx.Response(200, json=_load("detail_hybrid.json"))
    if path.endswith("/posting-200"):
        return httpx.Response(200, json=_load("detail_remote.json"))
    if path.endswith("/posting-300"):
        return httpx.Response(200, json=_load("detail_onsite.json"))
    if path.endswith("/posting-400"):
        return httpx.Response(200, json=_load("detail_sparse.json"))
    return httpx.Response(404, json={"ok": False, "error": "missing"})


def _source(**overrides) -> LeverPostingSource:
    client = overrides.pop("client", None) or httpx.Client(transport=httpx.MockTransport(_handler))
    defaults = {
        "site": "example",
        "company": "Example",
        "page_size": 2,
        "max_pages": 10,
        "min_interval_s": 0,
        "client": client,
        "sleep": lambda _delay: None,
    }
    defaults.update(overrides)
    return LeverPostingSource(**defaults)


def test_lever_source_matches_the_job_source_protocol() -> None:
    assert isinstance(_source(), JobSource)


def test_discover_paginates_and_skips_duplicates_and_malformed_rows() -> None:
    calls = {"lists": 0}

    def counting(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/postings/example":
            calls["lists"] += 1
        return _handler(request)

    jobs = list(_source(client=httpx.Client(transport=httpx.MockTransport(counting))).discover())

    assert calls["lists"] == 6
    assert [job["external_id"] for job in jobs] == ["posting-100", "posting-200", "posting-300", "posting-400"]
    assert all(job["title"] != "Account Executive" for job in jobs)
    hybrid = jobs[0]
    assert hybrid["url"] == APPLY_URL
    assert hybrid["external_id"] == "posting-100"
    assert hybrid["locations"] == [
        "Hybrid - McLean, VA",
        "McLean, VA",
        "Arlington, VA",
        "United States",
    ]
    assert "3 Locations" not in jobs[1]["locations"]
    assert jobs[1]["locations"] == ["United States", "Remote - United States"]
    assert jobs[1]["url"] == "https://jobs.lever.co/example/posting-200/apply"
    assert hybrid["posted_at"] == datetime.fromtimestamp(1756684800, tz=timezone.utc)
    sparse = jobs[3]
    assert sparse["locations"] == []
    assert sparse["description"] is None
    assert sparse["requisition_id"] is None
    assert sparse["posted_at"] is None
    assert sparse["url"] == "https://jobs.lever.co/example/posting-400/apply"


def test_fetch_detail_preserves_apply_url_id_and_raw_payload() -> None:
    source = _source()
    listing = next(iter(source.discover()))
    detailed = source.fetch_detail(RawJob.model_validate(listing))

    assert detailed is not None
    assert detailed["url"] == APPLY_URL
    assert detailed["external_id"] == "posting-100"
    assert detailed["raw"]["detail"]["workplaceType"] == "hybrid"
    assert detailed["raw"]["detail"]["hostedUrl"] != detailed["url"]
    assert "workplaceType" not in Job.__table__.columns


def test_ingestion_stores_lever_jobs_without_lever_columns(db_session) -> None:
    registry = SourceRegistry()
    registry.register(_source())
    report = IngestionService(registry).ingest(db_session, "lever:example")
    db_session.commit()

    assert report.rejected == 0
    assert report.created == 4
    jobs = list(db_session.scalars(select(Job)))
    by_external = {job.sources[0].external_id: job for job in jobs}
    hybrid = by_external["posting-100"]
    assert hybrid.apply_url == APPLY_URL
    assert hybrid.location_class == LocationClass.dmv
    assert hybrid.description_text == "Build software in McLean."
    assert hybrid.sources[0].raw["detail"]["id"] == "posting-100"
    remote = by_external["posting-200"]
    assert remote.apply_url == "https://jobs.lever.co/example/posting-200/apply"
    assert remote.location_class == LocationClass.us_remote
    assert "Remote - United States" in remote.locations
    assert by_external["posting-300"].location_class == LocationClass.us_other
    assert by_external["posting-400"].location_class == LocationClass.unknown


def test_transient_failure_is_retried() -> None:
    calls = {"jobs": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["jobs"] += 1
        if calls["jobs"] == 1:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json=[])

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
    with pytest.raises(LeverRequestError, match="refused"):
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
def test_public_lever_board_returns_internship_postings() -> None:
    with LeverPostingSource(
        site="palantir",
        company="Palantir",
        page_size=50,
        max_pages=2,
        min_interval_s=1.0,
    ) as source:
        found = list(source.discover())
        assert found
        listing = RawJob.model_validate(found[0])
        assert listing.url.startswith("https://jobs.lever.co/palantir/")
        assert listing.external_id
        detailed = source.fetch_detail(listing)
    assert detailed is not None
    parsed = RawJob.model_validate(detailed)
    assert parsed.url.startswith("https://jobs.lever.co/palantir/")
    assert parsed.external_id == listing.external_id
    assert parsed.raw["detail"]["applyUrl"] == parsed.url
