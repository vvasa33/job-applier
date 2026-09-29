"""Local agent inputs: career boards to poll, stored profile answers, and the submit policy."""

import tomllib
from pathlib import Path

from sqlalchemy.orm import Session

from jobhunter.apply.decisions import ApplicantData, DecisionSource, KnownFact
from jobhunter.apply.submission import SubmitPolicy
from jobhunter.config import Settings
from jobhunter.db.models import Application, UserSettings
from jobhunter.domain.enums import AutonomyLevel
from jobhunter.ingestion.ports import JobSource
from jobhunter.ingestion.sources import GreenhouseBoardSource, LeverPostingSource, WorkdayCareerSource


class AgentConfigError(Exception):
    """A local agent file is present but cannot be used."""


def sources_path(settings: Settings) -> Path:
    return settings.sources_file or settings.data_dir / "sources.toml"


def profile_path(settings: Settings) -> Path:
    return settings.profile_file or settings.data_dir / "profile.toml"


def load_sources(settings: Settings) -> list[JobSource]:
    """Boards from sources.toml. A missing file means no boards, not an error."""

    path = sources_path(settings)
    if not path.is_file():
        return []
    data = _toml(path)
    sources: list[JobSource] = []
    try:
        for entry in data.get("workday", []):
            sources.append(
                WorkdayCareerSource(
                    host=entry["host"],
                    tenant=entry["tenant"],
                    site=entry["site"],
                    company=entry["company"],
                    search_terms=tuple(entry.get("search_terms", ("intern", "co-op"))),
                )
            )
        for entry in data.get("greenhouse", []):
            sources.append(
                GreenhouseBoardSource(
                    board_token=entry["board_token"],
                    company=entry["company"],
                    search_terms=tuple(entry.get("search_terms", ("intern", "co-op"))),
                )
            )
        for entry in data.get("lever", []):
            sources.append(
                LeverPostingSource(
                    site=entry["site"],
                    company=entry["company"],
                    search_terms=tuple(entry.get("search_terms", ("intern", "co-op"))),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentConfigError(f"{path} has an invalid board entry: {exc}") from exc
    return sources


def load_profile_facts(settings: Settings) -> list[KnownFact]:
    """Explicit answers from profile.toml `[answers]`, keyed by decision key such as contact.email."""

    path = profile_path(settings)
    if not path.is_file():
        return []
    answers = _toml(path).get("answers", {})
    if not isinstance(answers, dict):
        raise AgentConfigError(f"{path}: [answers] must be a table")
    facts: list[KnownFact] = []
    for key, value in answers.items():
        if not isinstance(value, (str, int, float, bool)):
            raise AgentConfigError(f"{path}: answers.{key} must be a single value")
        text = ("yes" if value else "no") if isinstance(value, bool) else str(value)
        if text.strip():
            facts.append(KnownFact(key=str(key), value=text.strip(), confidence=1, source=DecisionSource.profile))
    return facts


def applicant_for(
    settings: Settings,
    application: Application,
    *,
    extra: list[KnownFact] | None = None,
    resume_text: str | None = None,
) -> ApplicantData:
    version = application.resume_version
    resume_path = None
    if version is not None and version.pdf_path and Path(version.pdf_path).is_file():
        resume_path = version.pdf_path
    return ApplicantData(
        facts=[*load_profile_facts(settings), *(extra or [])],
        resume_path=resume_path,
        resume_text=resume_text,
        job_title=application.job.title,
        company=application.job.company_name,
        job_description=application.job.description_text,
    )


def user_settings(session: Session) -> UserSettings:
    row = session.get(UserSettings, 1)
    if row is None:
        raise AgentConfigError("user settings are missing; run jobhunter db-upgrade")
    return row


def submit_policy(session: Session, settings: Settings) -> SubmitPolicy:
    return SubmitPolicy(
        autonomy=user_settings(session).autonomy_level or AutonomyLevel.assist,
        daily_auto_submit_cap=settings.agent_daily_auto_submits,
    )


def _toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentConfigError(f"{path} could not be read: {exc}") from exc
