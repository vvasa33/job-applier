from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RawJob(BaseModel):
    """Untrusted posting data from one source, before normalization."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    external_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    company: str | None = None
    locations: list[str] = Field(default_factory=list)
    description: str | None = None
    requisition_id: str | None = None
    posted_at: datetime | None = None
    term: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("company", "description", "requisition_id", "term", mode="before")
    @classmethod
    def blank_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("url")
    @classmethod
    def url_must_be_absolute(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) url")
        return value
