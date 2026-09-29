from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from jobhunter.db.models import Application, ApplicationEvent
from jobhunter.db.records import (
    JobSighting,
    create_application,
    record_application_event,
    record_job_sighting,
)
from jobhunter.domain.enums import ApplicationStatus, EventActor
from jobhunter.domain.errors import DuplicateApplication


def _job(db_session):
    return record_job_sighting(
        db_session,
        JobSighting(
            title="Backend Intern",
            company="Northwind",
            url="https://jobs.northwind.example/1",
            ats_type="lever",
            external_id="lv-1",
            board_key="northwind",
        ),
    )


def test_create_application(db_session) -> None:
    job = _job(db_session)
    application = create_application(db_session, job)
    db_session.commit()

    stored = db_session.get(Application, application.id)
    assert stored is not None
    assert stored.job_id == job.id
    assert stored.status == ApplicationStatus.found
    assert job.application.id == application.id


def test_second_application_for_the_same_job_is_rejected(db_session) -> None:
    job = _job(db_session)
    create_application(db_session, job)
    db_session.commit()

    try:
        create_application(db_session, job)
        raised = False
    except DuplicateApplication:
        raised = True
    assert raised

    db_session.add(Application(job_id=job.id))
    try:
        db_session.flush()
        constrained = False
    except IntegrityError:
        db_session.rollback()
        constrained = True
    assert constrained
    assert len(db_session.scalars(select(Application)).all()) == 1


def test_application_events_are_append_only(db_session) -> None:
    job = _job(db_session)
    application = create_application(db_session, job)
    first = record_application_event(
        db_session,
        application,
        event_type="queued",
        actor=EventActor.system,
        data={"status": "queued"},
    )
    second = record_application_event(
        db_session,
        application,
        event_type="note",
        actor=EventActor.user,
        data={"text": "looks relevant"},
    )
    db_session.commit()

    history = db_session.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application.id)
        .order_by(ApplicationEvent.id)
    ).all()
    assert [event.id for event in history] == [history[0].id, first.id, second.id]
    assert history[0].event_type == "application_created"
    assert history[1].data == {"status": "queued"}
    assert "updated_at" not in ApplicationEvent.__table__.columns

    try:
        db_session.execute(
            text("UPDATE application_events SET event_type = 'rewritten' WHERE id = :id"),
            {"id": first.id},
        )
        db_session.commit()
        blocked = False
    except (IntegrityError, OperationalError):
        db_session.rollback()
        blocked = True
    assert blocked
    assert db_session.get(ApplicationEvent, first.id).event_type == "queued"
