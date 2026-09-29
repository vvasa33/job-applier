from fastapi.testclient import TestClient

from jobhunter.api.app import create_app
from jobhunter.db.records import JobSighting, create_application, record_job_sighting
from jobhunter.domain.enums import ApplicationStatus, JobStatus, LocationClass


def _seed(session) -> None:
    remote = record_job_sighting(
        session,
        JobSighting(
            title="Data Intern",
            company="Northwind",
            url="https://jobs.example/northwind/data",
            ats_type="lever",
            external_id="nw-1",
            board_key="northwind",
            locations=("Remote - United States",),
            location_class=LocationClass.us_remote,
            is_internship=True,
            description_text="Analyze data for the internship.",
        ),
    )
    hybrid = record_job_sighting(
        session,
        JobSighting(
            title="Software Engineering Intern",
            company="Example",
            url="https://boards.example/example/jobs/10",
            ats_type="greenhouse",
            external_id="ex-10",
            board_key="example",
            locations=("Hybrid - McLean, VA",),
            location_class=LocationClass.dmv,
            is_internship=True,
        ),
    )
    record_job_sighting(
        session,
        JobSighting(
            title="Account Executive",
            company="Example",
            url="https://boards.example/example/jobs/11",
            ats_type="greenhouse",
            external_id="ex-11",
            board_key="example",
            locations=("Austin, Texas",),
            location_class=LocationClass.us_other,
            is_internship=False,
        ),
    )
    hybrid.status = JobStatus.shortlisted
    create_application(session, remote)
    session.commit()


def _client(settings):
    app = create_app(settings)
    session = app.state.session_factory()
    try:
        _seed(session)
    finally:
        session.close()
    return TestClient(app)


def test_job_list_filters_real_rows(settings) -> None:
    client = _client(settings)

    listed = client.get("/api/jobs")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 3
    assert body["companies"] == ["Example", "Northwind"]
    titles = {job["title"] for job in body["jobs"]}
    assert titles == {"Data Intern", "Software Engineering Intern", "Account Executive"}

    internships = client.get("/api/jobs", params={"internship": True, "q": "intern"})
    assert internships.json()["total"] == 2

    hybrid = client.get("/api/jobs", params={"workplace": "hybrid", "location": "McLean", "company": "Example"})
    assert [job["title"] for job in hybrid.json()["jobs"]] == ["Software Engineering Intern"]
    assert hybrid.json()["jobs"][0]["workplace"] == "hybrid"
    assert hybrid.json()["jobs"][0]["sources"] == ["greenhouse"]

    remote = client.get("/api/jobs", params={"workplace": "remote"})
    assert remote.json()["jobs"][0]["application_status"] == ApplicationStatus.queued.value

    saved = client.get("/api/jobs", params={"status": JobStatus.shortlisted.value})
    assert [job["title"] for job in saved.json()["jobs"]] == ["Software Engineering Intern"]

    unapplied = client.get("/api/jobs", params={"unapplied": True})
    assert unapplied.json()["total"] == 2

    onsite = client.get("/api/jobs", params={"workplace": "onsite", "internship": False})
    assert [job["title"] for job in onsite.json()["jobs"]] == ["Account Executive"]


def test_job_detail_returns_normalized_fields(settings) -> None:
    client = _client(settings)
    job_id = client.get("/api/jobs", params={"q": "Data"}).json()["jobs"][0]["id"]

    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["description_text"] == "Analyze data for the internship."
    assert body["apply_url"] == "https://jobs.example/northwind/data"
    assert body["source_records"][0]["external_id"] == "nw-1"
    assert body["requisition_id"] is None
    assert body["is_internship"] is True

    missing = client.get("/api/jobs/999999")
    assert missing.status_code == 404


def test_dashboard_counts_stored_jobs(settings) -> None:
    client = _client(settings)
    body = client.get("/api/dashboard").json()
    assert body["job_count"] == 3
    assert body["internship_count"] == 2
    assert body["remote_count"] == 1
    assert body["application_count"] == 1
    assert body["by_status"]["shortlisted"] == 1
    assert len(body["recent"]) == 3
