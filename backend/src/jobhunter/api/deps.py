from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from jobhunter.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
