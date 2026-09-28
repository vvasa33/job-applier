import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from jobhunter import __version__
from jobhunter.api.deps import get_db
from jobhunter.api.schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _health(db: Session) -> HealthResponse | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("database connectivity check failed")
        body = HealthResponse(status="degraded", database="unavailable", version=__version__)
        return JSONResponse(status_code=503, content=body.model_dump())
    return HealthResponse(status="ok", database="ok", version=__version__)


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse | JSONResponse:
    return _health(db)


@router.get("/api/health", response_model=HealthResponse)
def api_health(db: Session = Depends(get_db)) -> HealthResponse | JSONResponse:
    return _health(db)
