from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from jobhunter.ingestion.raw import RawJob


@dataclass(frozen=True)
class SourceIdentity:
    """Who produced a batch of raw jobs. Sources do not know about the database."""

    key: str
    ats_type: str
    board_key: str
    label: str
    company: str | None = None


@runtime_checkable
class JobSource(Protocol):
    """A job source discovers postings and, when it can, fills in detail.

    Implementations return plain data. They do not normalize, deduplicate, or write to SQLite.
    """

    def identify(self) -> SourceIdentity:
        """Identify this external source."""

    def discover(self) -> Iterable[Mapping[str, Any]]:
        """Return raw job records. Each item is validated by the ingestion service."""

    def fetch_detail(self, job: RawJob) -> Mapping[str, Any] | None:
        """Return a more complete raw record, or None when this source has nothing to add."""
