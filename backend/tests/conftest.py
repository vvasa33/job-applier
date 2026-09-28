from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from jobhunter.config import Settings, clear_settings_cache
from jobhunter.db.migrate import init_database
from jobhunter.db.session import build_engine, session_factory


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8765,
        data_dir=tmp_path,
        log_level="WARNING",
    )


@pytest.fixture
def db_session(settings) -> Iterator[Session]:
    init_database(settings)
    engine = build_engine(settings)
    session = session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()
