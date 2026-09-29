import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jobhunter.api.app import create_app
from jobhunter.applications import change_status, open_application
from jobhunter.db.models import Application, ApplicationEvent, Job
from jobhunter.db.records import JobSighting, add_master_resume, add_resume_version, record_job_sighting
from jobhunter.domain.application_flow import TRANSITIONS, allowed_targets
from jobhunter.domain.enums import ApplicationOutcome, ApplicationStatus, EventActor, JobStatus, ResumeVersionKind
from jobhunter.domain.errors import ApplicationTransitionError, DuplicateApplication


def _job(session, *, external_id: str = "nw-1", url: str = "https://jobs.example/northwind/1") -> Job:
    return record_job_sighting(
        session,
        JobSighting(
            title="Software Engineering Intern",
            company="Northwind",
            url=url,
            ats_type="lever",
            external_id=external_id,
        ),
    )


def _version(session, application: Application, *, sha: str = "abc123") -> int:
    resume = add_master_resume(session, path="/tmp/master.tex", sha256=f"master-{sha}")
    version = add_resume_version(
        session,
        resume=resume,
        kind=ResumeVersionKind.tailored,
        application=application,
        sha256=sha,
        tex_path=f"/tmp/applications/{application.id}/resume.tex",
        pdf_path=f"/tmp/applications/{application.id}/resume.pdf",
    )
    session.flush()
    return version.id


def test_every_status_has_an_explicit_transition_set() -> None:
    assert set(TRANSITIONS) == set(ApplicationStatus)
    for status, targets in TRANSITIONS.items():
        assert status not in targets
        assert targets <= set(ApplicationStatus)


def test_illegal_transitions_are_rejected(db_session) -> None:
    application = open_application(db_session, _job(db_session))
    for current in ApplicationStatus:
        application.status = current
        db_session.flush()
        for target in ApplicationStatus:
            if target in TRANSITIONS[current]:
                continue
            with pytest.raises(ApplicationTransitionError, match="cannot move"):
                change_status(
                    db_session,
                    application,
                    to=target,
                    actor=EventActor.user,
                    reason="not allowed",
                )
            assert application.status is current


def test_pipeline_records_history_timestamps_and_the_resume_version(db_session) -> None:
    job = _job(db_session)
    application = open_application(db_session, job, reason="Worth a look.")
    version_id = _version(db_session, application)
    steps = [
        (ApplicationStatus.matched, None),
        (ApplicationStatus.saved, None),
        (ApplicationStatus.tailoring, None),
        (ApplicationStatus.ready_to_apply, version_id),
        (ApplicationStatus.applying, None),
        (ApplicationStatus.waiting_for_user, None),
        (ApplicationStatus.applying, None),
        (ApplicationStatus.submitted, None),
        (ApplicationStatus.interview, None),
        (ApplicationStatus.offer, None),
    ]
    for target, resume_id in steps:
        change_status(
            db_session,
            application,
            to=target,
            actor=EventActor.user,
            reason=f"Moved to {target.value}.",
            resume_version_id=resume_id,
        )
    db_session.commit()

    stored = db_session.get(Application, application.id)
    assert stored.status is ApplicationStatus.offer
    assert stored.outcome is ApplicationOutcome.offer
    assert stored.resume_version_id == version_id
    assert stored.started_at is not None
    assert stored.submitted_at is not None
    assert stored.queued_at <= stored.started_at <= stored.submitted_at <= stored.status_changed_at
    assert db_session.get(Job, job.id).status is JobStatus.application_created

    history = db_session.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application.id)
        .order_by(ApplicationEvent.id)
    ).all()
    assert history[0].event_type == "application_created"
    assert history[0].data["to_status"] == "found"
    assert history[0].data["reason"] == "Worth a look."
    assert [event.data["to_status"] for event in history[1:]] == [step.value for step, _ in steps]
    assert history[4].data["resume_version_id"] == version_id
    assert allowed_targets(stored.status) == (ApplicationStatus.withdrawn,)


def test_ready_to_apply_without_a_resume_version_is_rejected(db_session) -> None:
    application = open_application(db_session, _job(db_session))
    change_status(db_session, application, to=ApplicationStatus.saved, actor=EventActor.user, reason="Saved.")
    change_status(db_session, application, to=ApplicationStatus.tailoring, actor=EventActor.user, reason="Tailor.")
    with pytest.raises(ApplicationTransitionError, match="tailored resume version"):
        change_status(
            db_session,
            application,
            to=ApplicationStatus.ready_to_apply,
            actor=EventActor.user,
            reason="Ready.",
        )
    assert application.status is ApplicationStatus.tailoring


def test_a_resume_version_from_another_application_is_rejected(db_session) -> None:
    first = open_application(db_session, _job(db_session))
    second = open_application(
        db_session,
        _job(db_session, external_id="nw-2", url="https://jobs.example/northwind/2"),
    )
    foreign_version = _version(db_session, second, sha="other")
    change_status(db_session, first, to=ApplicationStatus.saved, actor=EventActor.user, reason="Saved.")
    change_status(db_session, first, to=ApplicationStatus.tailoring, actor=EventActor.user, reason="Tailor.")
    with pytest.raises(ApplicationTransitionError, match="this application"):
        change_status(
            db_session,
            first,
            to=ApplicationStatus.ready_to_apply,
            actor=EventActor.user,
            reason="Ready.",
            resume_version_id=foreign_version,
        )


def test_terminal_statuses_and_blank_reasons_are_rejected(db_session) -> None:
    application = open_application(db_session, _job(db_session))
    change_status(db_session, application, to=ApplicationStatus.withdrawn, actor=EventActor.user, reason="Not a fit.")
    with pytest.raises(ApplicationTransitionError, match="allowed: none"):
        change_status(db_session, application, to=ApplicationStatus.saved, actor=EventActor.user, reason="Again.")
    assert application.outcome is ApplicationOutcome.withdrawn
    with pytest.raises(ApplicationTransitionError, match="needs a reason"):
        change_status(db_session, application, to=ApplicationStatus.saved, actor=EventActor.system, reason="   ")


def test_a_second_application_for_the_same_job_is_rejected(db_session) -> None:
    job = _job(db_session)
    first = open_application(db_session, job)
    with pytest.raises(DuplicateApplication, match=f"application {first.id}"):
        open_application(db_session, job)
    assert db_session.scalar(select(func.count()).select_from(Application)) == 1


def test_api_exposes_status_history_and_rejects_a_duplicate(settings) -> None:
    app = create_app(settings)
    session = app.state.session_factory()
    try:
        job = _job(session)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    client = TestClient(app)
    missing = client.get(f"/api/jobs/{job_id}/application")
    assert missing.status_code == 404

    opened = client.post(f"/api/jobs/{job_id}/application", json={"reason": "Saving it."})
    assert opened.status_code == 201
    body = opened.json()
    assert body["status"] == "found"
    assert body["history"][0]["to_status"] == "found"
    assert body["history"][0]["reason"] == "Saving it."
    assert body["started_at"] is None
    assert set(body["allowed_transitions"]) == {"matched", "saved", "withdrawn"}

    duplicate = client.post(f"/api/jobs/{job_id}/application", json={})
    assert duplicate.status_code == 409
    assert "already has application" in duplicate.json()["detail"]

    saved = client.post(
        f"/api/applications/{body['id']}/transitions",
        json={"to": "saved", "reason": "I want to apply."},
    )
    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"
    assert [event["to_status"] for event in saved.json()["history"]] == ["found", "saved"]

    illegal = client.post(
        f"/api/applications/{body['id']}/transitions",
        json={"to": "submitted", "reason": "Skip ahead."},
    )
    assert illegal.status_code == 400
    assert "cannot move" in illegal.json()["detail"]

    listed = client.get("/api/jobs", params={"application_status": "saved"})
    assert listed.json()["total"] == 1
    detail = client.get(f"/api/applications/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["job_id"] == job_id
