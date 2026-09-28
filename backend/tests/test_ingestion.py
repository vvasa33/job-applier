from sqlalchemy import func, select

from jobhunter.db.models import Job
from jobhunter.db.models import JobSource as StoredJobSource
from jobhunter.domain.enums import CsRelevance, LocationClass
from jobhunter.ingestion.normalize import best_location_class, normalize
from jobhunter.ingestion.ports import JobSource, SourceIdentity
from jobhunter.ingestion.raw import RawJob
from jobhunter.ingestion.registry import SourceRegistry
from jobhunter.ingestion.service import IngestionService
from tests.fake_source import FakeJobSource


def test_fake_source_satisfies_the_protocol() -> None:
    source = FakeJobSource()
    assert isinstance(source, JobSource)
    assert source.identify().key == "fake"


def test_normalize_cleans_messy_fields() -> None:
    raw = RawJob.model_validate(
        {
            "external_id": "nw-1",
            "title": "  SWE Intern  ",
            "url": "https://WWW.jobs.northwind.example/en-us/interns/1?utm_source=x",
            "company": "Northwind Labs, Inc.",
            "locations": [" McLean, VA ", "Austin, TX", "London, UK"],
            "description": "<p>Summer 2027 backend internship.</p>",
            "requisition_id": " R-100 ",
            "raw": {"id": "nw-1"},
        }
    )
    job = normalize(
        raw,
        SourceIdentity(key="fake", ats_type="fake", board_key="fixture", label="Fake", company="Northwind Labs"),
    )

    assert job.title == "SWE Intern"
    assert job.normalized_company == "northwind labs"
    assert job.canonical_url == "https://jobs.northwind.example/interns/1"
    assert job.locations == ("McLean, VA", "Austin, TX", "London, UK")
    assert job.location_class == LocationClass.dmv
    assert job.is_internship is True
    assert job.cs_relevance == CsRelevance.relevant
    assert job.term == "Summer 2027"
    assert job.requisition_id == "R-100"
    assert job.description_text == "Summer 2027 backend internship."
    assert job.dedup_key == "req:northwind labs:r-100"
    assert best_location_class(("London, UK", "Remote - United States")) == LocationClass.us_remote


def test_ingestion_stores_messy_jobs_without_duplicates(db_session) -> None:
    source = FakeJobSource()
    registry = SourceRegistry()
    registry.register(source)
    report = IngestionService(registry).ingest(db_session, "fake")
    db_session.commit()

    assert report.seen == 6
    assert report.rejected == 1
    assert report.created == 3
    assert report.duplicates == 2
    assert report.rejections[0].startswith("nw-bad:")
    assert db_session.scalar(select(func.count()).select_from(Job)) == 3

    jobs = {job.dedup_key: job for job in db_session.scalars(select(Job))}
    shared = jobs["req:northwind labs:r-100"]
    assert shared.title == "Software Engineer Intern"
    assert shared.location_class == LocationClass.dmv
    assert shared.locations == ["McLean, VA", "Austin, TX", "Remote - United States"]
    assert shared.is_internship is True
    assert shared.cs_relevance == CsRelevance.relevant
    assert shared.term == "Summer 2027"
    by_external = {item.external_id: item for item in shared.sources}
    assert set(by_external) == {"nw-1", "nw-1b"}
    assert by_external["nw-1"].ats_type == "fake"
    assert by_external["nw-1"].raw["id"] == "nw-1"
    assert by_external["nw-1b"].raw["id"] == "nw-1b"

    sparse = next(job for job in jobs.values() if job.apply_url.endswith("/interns/2"))
    assert sparse.company_name == "Northwind Labs"
    assert sparse.locations == []
    assert sparse.location_class == LocationClass.unknown
    assert sparse.requisition_id is None
    assert sparse.term is None
    assert sparse.description_text == "General internship program."
    assert sparse.cs_relevance == CsRelevance.unknown
    assert "nw-2" in source.detail_calls

    multi = next(job for job in jobs.values() if job.apply_url.endswith("/interns/3"))
    assert multi.location_class == LocationClass.dmv
    assert multi.locations == ["Washington, DC", "London, UK", "Remote, United States"]
    assert multi.term == "Summer 2027"
    assert (
        db_session.scalar(select(func.count()).select_from(StoredJobSource).where(StoredJobSource.job_id == multi.id))
        == 1
    )


def test_ingesting_the_same_source_twice_does_not_create_jobs(db_session) -> None:
    registry = SourceRegistry()
    registry.register(FakeJobSource())
    service = IngestionService(registry)
    first = service.ingest(db_session, "fake")
    second = service.ingest(db_session, "fake")
    db_session.commit()

    assert first.created == 3
    assert second.created == 0
    assert second.duplicates == 5
    assert db_session.scalar(select(func.count()).select_from(Job)) == 3


def test_registry_rejects_a_duplicate_key() -> None:
    registry = SourceRegistry()
    registry.register(FakeJobSource())
    try:
        registry.register(FakeJobSource())
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert registry.get("fake").identify().label == "Fake fixture"
