"""Start, stop, and watch the agent worker. The API only writes the desired state; the worker does the work."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobhunter.agent import store
from jobhunter.agent.config import AgentConfigError, load_sources, user_settings
from jobhunter.ai import ledger
from jobhunter.api.deps import get_db
from jobhunter.api.schemas import (
    AIDayOut,
    AIStatusOut,
    AgentActivityOut,
    AgentCurrentOut,
    AgentLimitsOut,
    AgentSettingsRequest,
    AgentStatusResponse,
)
from jobhunter.db.models import AgentAttempt, Application, Job
from jobhunter.domain.enums import AgentDesired, AgentPhase, ApplicationStatus, AutonomyLevel
from jobhunter.domain.time import utcnow
from jobhunter.llm.config import CHEAP, STRONG, api_key_from_environment, llm_model_for
from jobhunter.llm.errors import LLMError

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("", response_model=AgentStatusResponse)
def agent_status(request: Request, db: Session = Depends(get_db)) -> AgentStatusResponse:
    return _status(request, db)


@router.post("/start", response_model=AgentStatusResponse)
def start_agent(request: Request, db: Session = Depends(get_db)) -> AgentStatusResponse:
    store.set_desired(db, AgentDesired.running)
    db.commit()
    return _status(request, db)


@router.post("/stop", response_model=AgentStatusResponse)
def stop_agent(request: Request, db: Session = Depends(get_db)) -> AgentStatusResponse:
    store.set_desired(db, AgentDesired.stopped)
    db.commit()
    return _status(request, db)


@router.post("/discover", response_model=AgentStatusResponse)
def discover_now(request: Request, db: Session = Depends(get_db)) -> AgentStatusResponse:
    row = store.state(db)
    row.next_discovery_at = utcnow()
    store.log(db, "info", "discovery_requested", "Discovery requested; it runs when the agent is next free.")
    db.commit()
    return _status(request, db)


@router.put("/settings", response_model=AgentStatusResponse)
def update_settings(body: AgentSettingsRequest, request: Request, db: Session = Depends(get_db)) -> AgentStatusResponse:
    row = _user_settings(db)
    changes: list[str] = []
    if body.daily_llm_budget_usd is not None and body.daily_llm_budget_usd != row.daily_llm_budget_usd:
        row.daily_llm_budget_usd = body.daily_llm_budget_usd
        changes.append(f"daily AI budget ${body.daily_llm_budget_usd:.2f}")
    if body.autonomy_level is not None and body.autonomy_level != row.autonomy_level.value:
        row.autonomy_level = AutonomyLevel(body.autonomy_level)
        changes.append(f"autonomy {body.autonomy_level}")
    if body.discovery_interval_hours is not None and body.discovery_interval_hours != row.discovery_interval_hours:
        row.discovery_interval_hours = body.discovery_interval_hours
        state = store.state(db)
        if state.last_discovery_at is not None:
            state.next_discovery_at = store.aware(state.last_discovery_at) + timedelta(hours=body.discovery_interval_hours)
        changes.append(f"discovery every {body.discovery_interval_hours}h")
    if changes:
        store.log(db, "info", "settings_changed", "Settings changed: " + ", ".join(changes) + ".")
    db.commit()
    return _status(request, db)


@router.post("/applications/{application_id}/retry", response_model=AgentStatusResponse)
def retry_application(application_id: int, request: Request, db: Session = Depends(get_db)) -> AgentStatusResponse:
    """Let the agent try an application again after it gave up on it."""

    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    cleared = False
    for stage in ("apply", "prepare", "replay"):
        if store.attempt(db, stage, application_id) is not None:
            store.clear(db, stage, application_id)
            cleared = True
    if not cleared:
        raise HTTPException(status_code=409, detail="The agent has no failures recorded for this application")
    store.log(
        db,
        "info",
        "retry_requested",
        "You asked the agent to try this application again.",
        job_id=application.job_id,
        application_id=application.id,
    )
    db.commit()
    return _status(request, db)


@router.get("/activity", response_model=list[AgentActivityOut])
def agent_activity(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> list[AgentActivityOut]:
    return [
        AgentActivityOut(
            id=entry.id,
            created_at=entry.created_at,
            level=entry.level,
            kind=entry.kind,
            message=entry.message,
            job_id=entry.job_id,
            application_id=entry.application_id,
        )
        for entry in store.activity(db, limit)
    ]


def _status(request: Request, session: Session) -> AgentStatusResponse:
    settings = request.app.state.settings
    row = store.state(session)
    alive = store.worker_alive(row)
    preferences = _user_settings(session)
    since = utcnow() - timedelta(days=1)
    job = session.get(Job, row.current_job_id) if row.current_job_id is not None and alive else None
    try:
        sources, sources_error = len(load_sources(settings)), None
    except AgentConfigError as exc:
        sources, sources_error = 0, str(exc)
    waiting = int(
        session.scalar(
            select(func.count(Application.id)).where(Application.status == ApplicationStatus.waiting_for_user)
        )
        or 0
    )
    failing = int(
        session.scalar(
            select(func.count(AgentAttempt.id)).where(
                AgentAttempt.stage.in_(("apply", "prepare")),
                AgentAttempt.gave_up.is_(False),
                AgentAttempt.failures > 0,
            )
        )
        or 0
    )
    gave_up = int(session.scalar(select(func.count(AgentAttempt.id)).where(AgentAttempt.gave_up.is_(True))) or 0)
    phase = row.phase if alive else AgentPhase.stopped
    message = row.message
    if not alive:
        message = (
            "The worker process is not running. Start it with `jobhunter agent` (jobhunter dev starts it too)."
        )
    return AgentStatusResponse(
        desired=row.desired.value,
        phase=phase.value,
        alive=alive,
        pid=row.pid if alive else None,
        started_at=row.started_at if alive else None,
        heartbeat_at=row.heartbeat_at,
        last_discovery_at=row.last_discovery_at,
        next_discovery_at=row.next_discovery_at,
        current=AgentCurrentOut(
            job_id=row.current_job_id if alive else None,
            application_id=row.current_application_id if alive else None,
            company=None if job is None else job.company_name,
            title=None if job is None else job.title,
            step=row.current_step if alive else None,
        ),
        message=message,
        autonomy_level=preferences.autonomy_level.value,
        discovery_interval_hours=preferences.discovery_interval_hours,
        waiting_for_user=waiting,
        submitted_today=store.count_activity(session, "submitted", since),
        failing=failing,
        gave_up=gave_up,
        sources_configured=sources,
        sources_error=sources_error,
        limits=AgentLimitsOut(
            daily_applications=settings.agent_daily_applications,
            applications_today=store.count_activity(session, "apply_started", since),
            daily_auto_submits=settings.agent_daily_auto_submits,
            apply_interval_seconds=settings.agent_apply_interval_seconds,
            max_failures=settings.agent_max_failures,
        ),
        ai=_ai_status(session),
    )


def _ai_status(session: Session) -> AIStatusOut:
    try:
        api_key_from_environment()
        configured = True
    except LLMError:
        configured = False
    budget = ledger.budget_status(session)
    return AIStatusOut(
        configured=configured,
        cheap_model=llm_model_for(CHEAP),
        strong_model=llm_model_for(STRONG),
        day=budget.day,
        spent_today_usd=float(budget.spent),
        budget_usd=float(budget.budget),
        exceeded=budget.exceeded,
        by_purpose={purpose: float(cost) for purpose, cost in ledger.by_purpose(session, budget.day).items()},
        days=[
            AIDayOut(
                day=item.day,
                cost_usd=float(item.cost),
                saved_usd=float(item.saved),
                calls=item.calls,
                cached=item.cached,
                refused=item.refused,
            )
            for item in ledger.daily(session)
        ],
    )


def _user_settings(session: Session):
    try:
        return user_settings(session)
    except AgentConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
