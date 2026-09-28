import json
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
from jobhunter.ingestion.sources.workday import USER_AGENT, WorkdayCareerSource, WorkdayRequestError

FIXTURES = Path(__file__).parent / "fixtures" / "workday"
HOST = "example.wd5.myworkdayjobs.com"
APPLY_URL = "https://example.wd5.myworkdayjobs.com/Careers/job/McLean-Virginia/Software-Engineering-Intern_R-100"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["user-agent"] == USER_AGENT
    assert "cookie" not in {key.lower() for key in request.headers}
    assert "sec-ch-ua" not in {key.lower() for key in request.headers}
    if request.url.path.endswith("/jobs"):
        body = json.loads(request.content.decode())
        if body["searchText"] == "co-op":
            return httpx.Response(200, json=_load("jobs_coop.json"))
        if body["offset"] == 0:
            return httpx.Response(200, json=_load("jobs_page_1.json"))
        if body["offset"] == 2:
            return httpx.Response(200, json=_load("jobs_page_2.json"))
        return httpx.Response(200, json={"total": 3, "jobPostings": []})
    if request.url.path.endswith("Software-Engineering-Intern_R-100"):
        return httpx.Response(200, json=_load("detail_hybrid.json"))
    if request.url.path.endswith("Data-Intern_R-200"):
        return httpx.Response(200, json=_load("detail_remote.json"))
    if request.url.path.endswith("Campus-Ambassador_R-300"):
        return httpx.Response(200, json=_load("detail_onsite.json"))
    return httpx.Response(404, json={"error": "missing"})


def _source(**overrides) -> WorkdayCareerSource:
    client = overrides.pop("client", None) or httpx.Client(transport=httpx.MockTransport(_handler))
    defaults = {
        "host": HOST,
        "tenant": "example",
        "site": "Careers",
        "company": "Example",
        "page_size": 2,
        "max_pages": 5,
        "min_interval_s": 0,
        "client": client,
        "sleep": lambda _delay: None,
    }
    defaults.update(overrides)
    return WorkdayCareerSource(**defaults)


def test_workday_source_matches_the_job_source_protocol() -> None:
    assert isinstance(_source(), JobSource)


def test_discover_paginates_and_skips_duplicate_paths() -> None:
    jobs = list(_source().discover())

    assert [job["external_id"] for job in jobs] == [
        "/job/McLean-Virginia/Software-Engineering-Intern_R-100",
        "/job/Remote/Data-Intern_R-200",
        "/job/Austin-Texas/Campus-Ambassador_R-300",
    ]
    hybrid = jobs[0]
    assert hybrid["locations"] == [
        "Hybrid - McLean, Virginia, United States of America",
        "McLean, Virginia, United States of America",
    ]
    assert hybrid["requisition_id"] == "R-100"
    assert jobs[1]["locations"] == ["Remote"]
    assert "3 Locations" not in jobs[1]["locations"]


def test_fetch_detail_preserves_apply_url_id_and_raw_payload() -> None:
    source = _source()
    listing = next(iter(source.discover()))
    detailed = source.fetch_detail(RawJob.model_validate(listing))

    assert detailed is not None
    assert detailed["url"] == APPLY_URL
    assert detailed["external_id"] == "posting-100"
    assert detailed["requisition_id"] == "R-100"
    assert detailed["locations"] == [
        "Hybrid - McLean, Virginia, United States of America",
        "McLean, Virginia, United States of America",
        "Arlington, VA",
    ]
    assert detailed["raw"]["detail"]["jobPostingInfo"]["remoteType"] == "Hybrid"
    assert detailed["raw"]["externalPath"].endswith("R-100")
    assert "remoteType" not in Job.__table__.columns


def test_ingestion_stores_workday_jobs_without_workday_columns(db_session) -> None:
    registry = SourceRegistry()
    registry.register(_source())
    report = IngestionService(registry).ingest(db_session, "workday:example/Careers")
    db_session.commit()

    assert report.rejected == 0
    assert report.created == 3
    jobs = list(db_session.scalars(select(Job)))
    by_req = {job.requisition_id: job for job in jobs}
    hybrid = by_req["R-100"]
    assert hybrid.apply_url == APPLY_URL
    assert hybrid.location_class == LocationClass.dmv
    assert hybrid.sources[0].external_id == "posting-100"
    assert hybrid.sources[0].raw["detail"]["jobPostingInfo"]["id"] == "posting-100"
    remote = by_req["R-200"]
    assert remote.apply_url.endswith("Data-Intern_R-200?source=CareerSite")
    assert remote.location_class == LocationClass.us_remote
    assert "Remote - United States of America" in remote.locations
    assert by_req["R-300"].location_class == LocationClass.us_other


def test_transient_failure_is_retried() -> None:
    calls = {"jobs": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            calls["jobs"] += 1
            if calls["jobs"] == 1:
                return httpx.Response(503, json={"error": "unavailable"})
            return httpx.Response(200, json={"total": 0, "jobPostings": []})
        return httpx.Response(404, json={"error": "missing"})

    slept: list[float] = []
    source = _source(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=slept.append,
        search_terms=("intern",),
    )
    assert list(source.discover()) == []
    assert calls["jobs"] == 2
    assert slept == [0.5]


def test_access_refusal_is_not_retried() -> None:
    calls = {"jobs": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["jobs"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    source = _source(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(WorkdayRequestError, match="refused"):
        list(source.discover())
    assert calls["jobs"] == 1


def test_requests_are_paced() -> None:
    slept: list[float] = []
    now = {"t": 100.0}

    def sleep(delay: float) -> None:
        slept.append(delay)
        now["t"] += delay

    source = _source(min_interval_s=1.0, sleep=sleep, monotonic=lambda: now["t"], search_terms=("intern",))
    list(source.discover())
    assert slept
    assert slept[0] == pytest.approx(1.0)


@pytest.mark.live
def test_public_workday_board_returns_internship_postings() -> None:
    with WorkdayCareerSource(
        host="generalmotors.wd5.myworkdayjobs.com",
        tenant="generalmotors",
        site="Careers_GM",
        company="General Motors",
        search_terms=("intern",),
        page_size=5,
        max_pages=1,
        min_interval_s=1.0,
    ) as source:
        found = list(source.discover())
        assert found
        listing = RawJob.model_validate(found[0])
        assert listing.url.startswith("https://generalmotors.wd5.myworkdayjobs.com/")
        assert listing.external_id
        detailed = source.fetch_detail(listing)
    assert detailed is not None
    parsed = RawJob.model_validate(detailed)
    assert parsed.url.startswith("https://generalmotors.wd5.myworkdayjobs.com/")
    assert parsed.requisition_id
    assert parsed.raw["detail"]["jobPostingInfo"]["externalUrl"] == parsed.url
