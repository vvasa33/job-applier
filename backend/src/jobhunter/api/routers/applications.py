"""Application status, the visible apply flow, and history."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
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
    ReviewQueueItem,
    ReviewRequest,
    RunApplicationRequest,
    TransitionRequest,
)
from jobhunter.apply.decisions import ApplicantData, DecisionSource, KnownFact
from jobhunter.apply.runner import (
    page_context,
    pending_fields,
    refuse_submit,
    respond,
    resume_page,
    run_page,
    waiting_applications,
)
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
    request: Request,
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
    return _response(db, application, _screenshot_dir(request))


@router.get("/jobs/{job_id}/application", response_model=ApplicationResponse)
def read_job_application(job_id: int, request: Request, db: Session = Depends(get_db)) -> ApplicationResponse:
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    application = application_for_job(db, job_id)
    if application is None:
        raise HTTPException(status_code=404, detail="This job has no application")
    return _response(db, application, _screenshot_dir(request))


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
def read_application(application_id: int, request: Request, db: Session = Depends(get_db)) -> ApplicationResponse:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _response(db, application, _screenshot_dir(request))


@router.post("/applications/{application_id}/transitions", response_model=ApplicationResponse)
def transition_application(
    application_id: int,
    body: TransitionRequest,
    request: Request,
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
    return _response(db, application, _screenshot_dir(request))


@router.get("/review", response_model=list[ReviewQueueItem])
def review_queue(request: Request, db: Session = Depends(get_db)) -> list[ReviewQueueItem]:
    directory = _screenshot_dir(request)
    return [_review_item(db, application, directory) for application in waiting_applications(db)]


@router.get("/applications/{application_id}/screenshot")
def application_screenshot(application_id: int, request: Request, db: Session = Depends(get_db)) -> FileResponse:
    application = _application(db, application_id)
    directory = _screenshot_dir(request)
    context = page_context(db, application, directory)
    path = directory / f"application-{application.id}.png"
    if not context["has_screenshot"] or not path.is_file():
        raise HTTPException(status_code=404, detail="No page image is available")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/applications/{application_id}/review", response_model=ApplicationResponse)
def review_application(
    application_id: int,
    body: ReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    application = _application(db, application_id)
    browser = None
    owned = False
    if body.action != "stop":
        browser, owned = _browser(request)
    try:
        respond(
            db,
            application,
            browser,
            _applicant(application, body),
            action=body.action,
            field_id=body.field_id,
            value=body.value,
            actor=EventActor.user,
            screenshot_dir=_screenshot_dir(request),
        )
        db.commit()
    except ApplicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if owned and browser is not None:
            browser.close()
    return _response(db, application, _screenshot_dir(request))


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
        run_page(
            db,
            application,
            browser,
            _applicant(application, body or RunApplicationRequest()),
            screenshot_dir=_screenshot_dir(request),
        )
        db.commit()
    except ApplicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if owned:
            browser.close()
    return _response(db, application, _screenshot_dir(request))


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
            screenshot_dir=_screenshot_dir(request),
        )
        db.commit()
    except ApplicationTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if owned:
            browser.close()
    return _response(db, application, _screenshot_dir(request))


@router.post("/applications/{application_id}/submit", response_model=ApplicationResponse)
def submit_application(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    application = _application(db, application_id)
    try:
        refuse_submit(db, application, actor=EventActor.user)
    except SubmitRefused as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _response(db, application, _screenshot_dir(request))


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


def _screenshot_dir(request: Request) -> Path:
    return request.app.state.settings.data_dir / "screenshots"


def _applicant(
    application: Application,
    body: RunApplicationRequest | ResumeApplicationRequest | ReviewRequest,
) -> ApplicantData:
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


def _response(session: Session, application: Application, screenshot_dir: Path) -> ApplicationResponse:
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
    context = page_context(session, application, screenshot_dir)
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
        company=application.job.company_name,
        title=application.job.title,
        page_url=context["page_url"],
        page_title=context["page_title"],
        has_screenshot=context["has_screenshot"],
        waiting_reason=context["waiting_reason"],
    )


def _review_item(session: Session, application: Application, screenshot_dir: Path) -> ReviewQueueItem:
    questions = pending_fields(session, application)
    context = page_context(session, application, screenshot_dir)
    first = questions[0] if questions else None
    return ReviewQueueItem(
        application_id=application.id,
        job_id=application.job_id,
        company=application.job.company_name,
        title=application.job.title,
        waiting_since=application.status_changed_at,
        question_count=len(questions),
        question_label=None if first is None else str(first.get("label") or first.get("field_id") or ""),
        waiting_reason=context["waiting_reason"],
        has_screenshot=context["has_screenshot"],
    )


def _pending(field: dict) -> PendingFieldOut:
    proposed = field.get("proposed_value")
    options = field.get("options")
    confidence = field.get("confidence")
    return PendingFieldOut(
        field_id=str(field.get("field_id") or ""),
        label=str(field.get("label") or ""),
        action=str(field.get("action") or ""),
        proposed_value=proposed if isinstance(proposed, str) else None,
        reasoning=str(field.get("reasoning") or ""),
        required=bool(field.get("required")),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else 0,
        type=str(field.get("type") or ""),
        options=[str(option) for option in options] if isinstance(options, list) else [],
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
