"""Application status and history. These routes record decisions; they do not drive a browser."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.api.deps import get_db
from jobhunter.api.schemas import (
    ApplicationEventOut,
    ApplicationResponse,
    OpenApplicationRequest,
    ResumeVersionOut,
    TransitionRequest,
)
from jobhunter.applications import application_for_job, change_status, open_application
from jobhunter.db.models import Application, ApplicationEvent, Job, ResumeVersion
from jobhunter.domain.application_flow import allowed_targets
from jobhunter.domain.enums import EventActor
from jobhunter.domain.errors import ApplicationTransitionError, DuplicateApplication

router = APIRouter(prefix="/api", tags=["applications"])


@router.post("/jobs/{job_id}/application", response_model=ApplicationResponse, status_code=201)
def open_job_application(
    job_id: int,
    body: OpenApplicationRequest | None = None,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    reason = "Application opened." if body is None else body.reason
    try:
        application = open_application(db, job, actor=EventActor.user, reason=reason)
        db.commit()
    except DuplicateApplication as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApplicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _response(db, application)


@router.get("/jobs/{job_id}/application", response_model=ApplicationResponse)
def read_job_application(job_id: int, db: Session = Depends(get_db)) -> ApplicationResponse:
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    application = application_for_job(db, job_id)
    if application is None:
        raise HTTPException(status_code=404, detail="This job has no application")
    return _response(db, application)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
def read_application(application_id: int, db: Session = Depends(get_db)) -> ApplicationResponse:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _response(db, application)


@router.post("/applications/{application_id}/transitions", response_model=ApplicationResponse)
def transition_application(
    application_id: int,
    body: TransitionRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        change_status(
            db,
            application,
            to=body.to,
            actor=EventActor.user,
            reason=body.reason,
            resume_version_id=body.resume_version_id,
        )
        db.commit()
    except ApplicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _response(db, application)


def _response(session: Session, application: Application) -> ApplicationResponse:
    events = session.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application.id)
        .order_by(ApplicationEvent.id)
    ).all()
    versions = session.scalars(
        select(ResumeVersion)
        .where(ResumeVersion.application_id == application.id)
        .order_by(ResumeVersion.id)
    ).all()
    current = None
    if application.resume_version_id is not None:
        current = session.get(ResumeVersion, application.resume_version_id)
    return ApplicationResponse(
        id=application.id,
        job_id=application.job_id,
        status=application.status.value,
        allowed_transitions=[item.value for item in allowed_targets(application.status)],
        opened_at=application.queued_at,
        status_changed_at=application.status_changed_at,
        started_at=application.started_at,
        submitted_at=application.submitted_at,
        resume_version=None if current is None else _version(current),
        resume_versions=[_version(version) for version in versions],
        history=[_event(event) for event in events],
    )


def _version(version: ResumeVersion) -> ResumeVersionOut:
    return ResumeVersionOut(
        id=version.id,
        sha256=version.sha256,
        tex_path=version.tex_path,
        pdf_path=version.pdf_path,
        diff_path=version.diff_path,
        created_at=version.created_at,
    )


def _event(event: ApplicationEvent) -> ApplicationEventOut:
    data = event.data or {}
    return ApplicationEventOut(
        id=event.id,
        actor=event.actor.value,
        event_type=event.event_type,
        from_status=data.get("from_status"),
        to_status=data.get("to_status"),
        reason=data.get("reason"),
        resume_version_id=data.get("resume_version_id"),
        created_at=event.created_at,
    )
