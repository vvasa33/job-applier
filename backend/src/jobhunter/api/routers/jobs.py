from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from jobhunter.ai import ledger
from jobhunter.api.deps import get_db
from jobhunter.api.jobs_query import WORKPLACES, companies, dashboard_counts, job_detail, list_jobs, workplace_of
from jobhunter.api.schemas import DashboardResponse, JobDetail, JobListResponse, JobSourceOut, JobSummary, RequirementOut
from jobhunter.db.models import Job
from jobhunter.domain.enums import ApplicationStatus, JobStatus

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/dashboard", response_model=DashboardResponse)
def read_dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    counts = dashboard_counts(db)
    recent, _total = list_jobs(db, limit=5)
    return DashboardResponse(
        job_count=counts["jobs"],
        internship_count=counts["internships"],
        remote_count=counts["remote"],
        application_count=counts["applications"],
        by_status=counts["by_status"],
        recent=[_summary(job) for job in recent],
    )


@router.get("/jobs", response_model=JobListResponse)
def read_jobs(
    q: str | None = None,
    internship: bool | None = None,
    location: str | None = None,
    workplace: str | None = Query(default=None),
    company: str | None = None,
    status: JobStatus | None = None,
    application_status: ApplicationStatus | None = None,
    unapplied: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> JobListResponse:
    if workplace is not None and workplace not in WORKPLACES:
        raise HTTPException(status_code=422, detail="workplace must be remote, hybrid, or onsite")
    rows, total = list_jobs(
        db,
        q=q,
        internship=internship,
        location=location,
        workplace=workplace,
        company=company,
        status=status,
        application_status=application_status,
        unapplied=unapplied,
        limit=limit,
        offset=offset,
    )
    return JobListResponse(total=total, companies=companies(db), jobs=[_summary(job) for job in rows])


@router.get("/jobs/{job_id}", response_model=JobDetail)
def read_job(job_id: int, db: Session = Depends(get_db)) -> JobDetail:
    job = job_detail(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    summary = _summary(job)
    return JobDetail(
        **summary.model_dump(),
        normalized_title=job.normalized_title,
        normalized_company=job.normalized_company,
        cs_relevance=job.cs_relevance.value,
        term=job.term,
        description_text=job.description_text,
        apply_url=job.apply_url,
        canonical_apply_url=job.canonical_apply_url,
        requisition_id=job.requisition_id,
        posted_at=job.posted_at,
        closed_at=job.closed_at,
        status_reason=job.status_reason,
        dedup_key=job.dedup_key,
        possible_duplicate_of=job.possible_duplicate_of,
        source_records=[
            JobSourceOut(
                ats_type=source.ats_type,
                board_key=source.board_key,
                external_id=source.external_id,
                url=source.url,
                title=source.title,
                first_seen_at=source.first_seen_at,
                last_seen_at=source.last_seen_at,
            )
            for source in sorted(job.sources, key=lambda source: (source.first_seen_at, source.id))
        ],
        requirements=[
            RequirementOut(kind=item.kind.value, value=item.value, verified=item.verified)
            for item in job.requirements
        ],
        ai_cost_usd=float(ledger.total_for(db, job_id=job.id)),
    )


def _summary(job: Job) -> JobSummary:
    sources = sorted({source.ats_type for source in job.sources})
    return JobSummary(
        id=job.id,
        title=job.title,
        company=job.company_name,
        locations=list(job.locations or []),
        location_class=job.location_class.value,
        workplace=workplace_of(job.locations, job.location_class),
        sources=sources or ([job.ats_type] if job.ats_type else []),
        first_seen_at=job.first_seen_at,
        is_internship=job.is_internship,
        status=job.status.value,
        application_status=None if job.application is None else job.application.status.value,
        ats_type=job.ats_type,
    )
