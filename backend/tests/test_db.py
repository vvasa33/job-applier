from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from jobhunter.db.base import Base
from jobhunter.db.session import build_engine, database_path


def test_engine_opens_sqlite_with_expected_pragmas(settings) -> None:
    engine = build_engine(settings)
    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar()
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar()
        connection.execute(text("SELECT 1")).scalar()
    engine.dispose()

    assert database_path(settings).is_file()
    assert journal_mode == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 5000


def test_core_tables_are_registered() -> None:
    import jobhunter.db.models  # noqa: F401

    assert issubclass(Base, DeclarativeBase)
    assert set(Base.metadata.tables) == {
        "jobs",
        "job_sources",
        "job_requirements",
        "resumes",
        "resume_versions",
        "applications",
        "application_events",
        "application_answers",
        "agent_runs",
        "agent_state",
        "agent_activity",
        "agent_attempts",
        "ai_cache",
        "ai_usage",
        "user_settings",
    }
