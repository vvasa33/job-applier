from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from jobhunter.api.deps import get_db, get_settings
from jobhunter.api.schemas import CompileResponse, ValidateResponse
from jobhunter.config import Settings
from jobhunter.resume.master import MasterResumeError, compile_master, validate_master

router = APIRouter(prefix="/api/resume")


@router.post("/validate", response_model=ValidateResponse)
def validate(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ValidateResponse:
    try:
        report = validate_master(db, settings)
    except MasterResumeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ValidateResponse(
        path=report.path,
        sha256=report.sha256,
        resume_id=report.resume_id,
        sections=[
            {"name": section.name, "kind": section.kind, "entries": list(section.entries)}
            for section in report.parsed.sections
        ],
    )


@router.post("/compile", response_model=CompileResponse)
def compile_resume(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CompileResponse:
    try:
        report = compile_master(db, settings)
    except MasterResumeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CompileResponse(path=report.path, sha256=report.sha256, pdf_path=report.pdf_path)
