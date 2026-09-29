"""Content hashes for cache keys. Cosmetic changes (markup, spacing) do not change a hash; wording does."""

import hashlib
import html
import json
import re

from jobhunter.matching.profiles import JobProfile

_TAG = re.compile(r"<[^>]+>")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?)\]])")


def normalized_text(text: str | None) -> str:
    if not text:
        return ""
    collapsed = " ".join(_TAG.sub(" ", html.unescape(text)).split())
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", collapsed)


def digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def job_content_hash(job: JobProfile) -> str:
    """Exactly the job fields a prompt sees: title, company, locations, and description."""

    return digest(
        {
            "title": normalized_text(job.title),
            "company": normalized_text(job.company),
            "locations": sorted(normalized_text(item) for item in job.locations),
            "description": normalized_text(job.description_text),
        }
    )
