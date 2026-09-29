import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, TomlConfigSettingsSource

from jobhunter.local_env import load_local_env

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "jobhunter"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBHUNTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    data_dir: Path = Field(default_factory=default_data_dir)
    log_level: str = "INFO"
    master_resume: Path | None = None
    sources_file: Path | None = None
    profile_file: Path | None = None
    agent_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    agent_apply_interval_seconds: int = Field(default=120, ge=0)
    agent_daily_applications: int = Field(default=10, ge=0)
    agent_daily_auto_submits: int = Field(default=3, ge=0)
    agent_prepare_per_cycle: int = Field(default=5, ge=0)
    agent_max_failures: int = Field(default=3, ge=1, le=20)
    agent_max_replays: int = Field(default=5, ge=1, le=50)

    @field_validator("host")
    @classmethod
    def host_must_be_local(cls, value: str) -> str:
        if value not in _LOCAL_HOSTS:
            raise ValueError("host must be 127.0.0.1, localhost, or ::1")
        return value

    @field_validator("log_level")
    @classmethod
    def log_level_must_be_known(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in _LOG_LEVELS:
            raise ValueError(f"log_level must be one of: {', '.join(sorted(_LOG_LEVELS))}")
        return normalized

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        config_path = os.environ.get("JOBHUNTER_CONFIG", "").strip()
        if config_path:
            path = Path(config_path)
            if not path.is_file():
                raise ValueError(f"JOBHUNTER_CONFIG does not point at a file: {path}")
            with path.open("rb") as handle:
                tomllib.load(handle)
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=path))
        sources.append(file_secret_settings)
        return tuple(sources)

    def bind_host(self) -> str:
        if self.host == "localhost":
            return "127.0.0.1"
        return self.host


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_local_env()
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
