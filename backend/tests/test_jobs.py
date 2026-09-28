from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from jobhunter.db.models import Job, JobSource
from jobhunter.db.records import JobSighting, record_job_sighting
from jobhunter.domain.enums import JobStatus


def _sighting(**overrides) -> JobSighting:
    fields = {
        "title": "Software Engineering Intern",
        "company": "Acme Inc.",
        "url": "https://acme.example/jobs/1",
        "ats_type": "greenhouse",
        "external_id": "gh-1",
        "board_key": "acme",
    }
    fields.update(overrides)
    return JobSighting(**fields)


def test_create_job(db_session) -> None:
    job = record_job_sighting(db_session, _sighting())
    db_session.commit()

    stored = db_session.get(Job, job.id)
    assert stored is not None
    assert stored.status == JobStatus.discovered
    assert stored.company_name == "Acme Inc."
    assert stored.normalized_company == "acme"
    assert stored.dedup_key == "url:https://acme.example/jobs/1"
    assert len(stored.sources) == 1
    assert stored.sources[0].external_id == "gh-1"


def test_same_requisition_from_two_sources_is_one_job(db_session) -> None:
    first = record_job_sighting(
        db_session,
        _sighting(
            company="Acme Inc.",
            requisition_id="R-42",
            url="https://acme.wd5.myworkdayjobs.com/job/R-42",
            ats_type="workday",
            external_id="wd-42",
            board_key="acme",
        ),
    )
    second = record_job_sighting(
        db_session,
        _sighting(
            company="Acme",
            requisition_id="r-42",
            url="https://boards.greenhouse.io/acme/jobs/999",
            ats_type="greenhouse",
            external_id="gh-999",
            board_key="acme",
        ),
    )
    db_session.commit()

    assert first.id == second.id
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    sources = db_session.scalars(select(JobSource).where(JobSource.job_id == first.id)).all()
    assert {source.ats_type for source in sources} == {"workday", "greenhouse"}


def test_repeated_url_does_not_create_another_job_or_source(db_session) -> None:
    record_job_sighting(db_session, _sighting())
    record_job_sighting(
        db_session,
        _sighting(url="https://WWW.acme.example/jobs/1?utm_source=newsletter", external_id="gh-1-again"),
    )
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobSource)) == 1


def test_duplicate_dedup_key_is_rejected(db_session) -> None:
    job = record_job_sighting(db_session, _sighting())
    db_session.add(
        Job(
            title="Other title",
            normalized_title="other title",
            company_name="Other",
            normalized_company="other",
            dedup_key=job.dedup_key,
        )
    )
    try:
        db_session.flush()
        raised = False
    except IntegrityError:
        db_session.rollback()
        raised = True
    assert raised
