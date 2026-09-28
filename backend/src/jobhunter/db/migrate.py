from pathlib import Path

from alembic import command
from alembic.config import Config

from jobhunter.config import Settings
from jobhunter.db.session import database_path, sqlite_url


def migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def init_database(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    config = Config(str(migrations_dir().parent / "alembic.ini"))
    config.set_main_option("script_location", str(migrations_dir()))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path(settings)))
    command.upgrade(config, "head")
