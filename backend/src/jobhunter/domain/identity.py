import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_COMPANY_SUFFIX = re.compile(r"\b(incorporated|corporation|company|corp|inc|llc|ltd|co)\b\.?")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_LOCALE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")
_TRACKING_EXACT = frozenset({"gh_src", "source", "lever-source"})


def normalize_name(value: str) -> str:
    text = value.casefold()
    text = _COMPANY_SUFFIX.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    path = _strip_locale(parsed.path).rstrip("/") or "/"
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=False)
        if not _is_tracking_param(key)
    ]
    query.sort()
    scheme = (parsed.scheme or "https").casefold()
    return urlunparse((scheme, host, path, "", urlencode(query), ""))


def make_dedup_key(
    *,
    company: str,
    requisition_id: str | None,
    apply_url: str | None,
    title: str,
    term: str | None,
) -> str:
    company_key = normalize_name(company)
    requisition = (requisition_id or "").strip().casefold()
    if requisition:
        return f"req:{company_key}:{requisition}"
    if apply_url and apply_url.strip():
        return f"url:{canonical_url(apply_url)}"
    term_key = (term or "").strip().casefold()
    return f"title:{company_key}|{normalize_name(title)}|{term_key}"


def _strip_locale(path: str) -> str:
    parts = path.split("/")
    if len(parts) > 2 and _LOCALE.match(parts[1]):
        del parts[1]
    return "/".join(parts)


def _is_tracking_param(key: str) -> bool:
    lowered = key.casefold()
    return lowered.startswith("utm_") or lowered in _TRACKING_EXACT
