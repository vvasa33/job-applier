"""Application status, the visible apply flow, and history."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.api.deps import get_db
from jobhunter.api.schemas import (
    ApplicantFactIn,
    ApplicationEventOut,
    ApplicationResponse,
    OpenApplicationRequest,
    PendingFieldOut,
    ResumeApplicationRequest,
    ResumeVersionOut,
    RunApplicationRequest,
    TransitionRequest,
)
from jobhunter.apply.decisions import ApplicantData, DecisionSource, KnownFact
from jobhunter.apply.runner import pending_fields, refuse_submit, resume_page, run_page
from jobhunter.applications import application_for_job, change_status, open_application
from jobhunter.browser.errors import SubmitRefused
from jobhunter.browser.manager import BrowserManager
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


@router.post("/applications/{application_id}/run", response_model=ApplicationResponse)
def run_application(
    application_id: int,
    request: Request,
    body: RunApplicationRequest | None = None,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    application = _application(db, application_id)
    browser, owned = _browser(request)
    try:
        run_page(db, application, browser, _applicant(application, body or RunApplicationRequest()))
        db.commit()
    except ApplicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if owned:
            browser.close()
    return _response(db, application)


@router.post("/applications/{application_id}/answers", response_model=ApplicationResponse)
def resume_application(
    application_id: int,
    body: ResumeApplicationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    application = _application(db, application_id)
    browser, owned = _browser(request)
    try:
        resume_page(
            db,
            application,
            browser,
            _applicant(application, body),
            body.answers,
            actor=EventActor.user,
        )
        db.commit()
    except ApplicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if owned:
            browser.close()
    return _response(db, application)


@router.post("/applications/{application_id}/submit", response_model=ApplicationResponse)
def submit_application(application_id: int, db: Session = Depends(get_db)) -> ApplicationResponse:
    application = _application(db, application_id)
    try:
        refuse_submit(db, application, actor=EventActor.user)
    except SubmitRefused as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _response(db, application)


def _application(session: Session, application_id: int) -> Application:
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


def _browser(request: Request) -> tuple[BrowserManager, bool]:
    factory = getattr(request.app.state, "browser_factory", None)
    if factory is not None:
        return factory(), False
    manager = BrowserManager(profile_dir=request.app.state.settings.data_dir / "browser-profile")
    manager.open()
    return manager, True


def _applicant(application: Application, body: RunApplicationRequest | ResumeApplicationRequest) -> ApplicantData:
    return ApplicantData(
        facts=[_fact(fact) for fact in body.facts],
        resume_path=body.resume_path,
        resume_text=body.resume_text,
        job_title=application.job.title,
        company=application.job.company_name,
        job_description=application.job.description_text,
    )


def _fact(fact: ApplicantFactIn) -> KnownFact:
    try:
        source = DecisionSource(fact.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown fact source {fact.source}") from exc
    return KnownFact(key=fact.key, value=fact.value, confidence=fact.confidence, source=source)


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
        pending_fields=[_pending(field) for field in pending_fields(session, application)],
    )


def _pending(field: dict) -> PendingFieldOut:
    return PendingFieldOut(
        field_id=str(field.get("field_id") or ""),
        label=str(field.get("label") or ""),
        action=str(field.get("action") or ""),
        proposed_value=field.get("proposed_value"),
        reasoning=str(field.get("reasoning") or ""),
        required=bool(field.get("required")),
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
