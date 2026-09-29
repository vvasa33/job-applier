"""Deterministic checks that a generated bullet only restates its cited master entries."""

import re

from jobhunter.matching.signals import _SKILLS
from jobhunter.resume.spans import ResumeDocument

MAX_BULLET_CHARS = 400

_MONTHS = frozenset(
    "january february march april june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec".split()
)
_QUANTITY_WORDS = frozenset(
    "one two three four five six seven eight nine ten eleven twelve fifteen twenty thirty fifty "
    "hundred hundreds thousand thousands million millions billion billions dozen dozens "
    "percent double doubled triple tripled half".split()
)
_CLAIM_WORDS = frozenset(
    "led lead leading managed manage managing mentored mentoring supervised supervising directed "
    "founded cofounded co-founded owned spearheaded headed launched won grew award awarded awards patent "
    "patented published publication promoted hired recruited trained taught certified certification "
    "degree phd masters bachelor bachelors gpa honors scholarship".split()
)
_REWORDING = frozenset(
    "built wrote made ran drove kept set "
    "developed created implemented designed written improved worked delivered shipped "
    "automated maintained engineered programmed added reduced increased resolved supported "
    "collaborated applied leveraged utilized enabled helped contributed integrated deployed tested "
    "analyzed optimized streamlined refactored produced prepared handled performed including "
    "using used across within through while which that with from into their this these those "
    "various multiple several also both each".split()
)
_STOPWORDS = frozenset(
    "the and for with that this from into over than then also when where about after before "
    "between during were been being have having will would could should such very more most "
    "other some them they your ours".split()
)


def skills_vocabulary(document: ResumeDocument) -> frozenset[str]:
    terms = set(_SKILLS)
    for bullet in document.bullets.values():
        if bullet.section_kind != "skills":
            continue
        for part in re.split(r"[,;:|/]", bullet.plain):
            term = part.strip().casefold()
            if len(term) >= 2:
                terms.add(term)
    return frozenset(terms)


def trace_problems(text: str, evidence: str, vocabulary: frozenset[str]) -> list[str]:
    """Return reasons the text is not traceable to evidence. Empty means it passed."""
    if not text:
        return ["is empty"]
    if len(text) > MAX_BULLET_CHARS:
        return [f"is longer than {MAX_BULLET_CHARS} characters"]
    if any(char in text for char in "\\{}"):
        return ["contains LaTeX markup; bullets must be plain text"]

    problems: list[str] = []
    lowered = text.casefold()
    source = evidence.casefold()
    source_words = set(_words(source))
    source_stems = {_stem(word) for word in source_words}

    numbers = sorted(_numbers(text) - _numbers(evidence))
    if numbers:
        problems.append(f"adds numbers or metrics not in the cited evidence: {', '.join(numbers)}")

    words = _words(lowered)
    quantities = sorted({word for word in words if word in _QUANTITY_WORDS} - source_words)
    if quantities:
        problems.append(f"adds quantities not in the cited evidence: {', '.join(quantities)}")
    months = sorted({word for word in words if word in _MONTHS} - source_words)
    if months:
        problems.append(f"adds dates not in the cited evidence: {', '.join(months)}")
    claims = sorted({word for word in words if word in _CLAIM_WORDS} - source_words)
    if claims:
        problems.append(f"adds claims not in the cited evidence: {', '.join(claims)}")

    skills = sorted(term for term in vocabulary if _has_term(lowered, term) and not _has_term(source, term))
    if skills:
        problems.append(f"adds skills not in the cited evidence: {', '.join(skills)}")

    names = _new_names(text, source)
    if names:
        problems.append(f"adds names not in the cited evidence: {', '.join(names)}")

    content = [word for word in words if len(word) >= 4 and word not in _STOPWORDS and word not in _REWORDING]
    novel = sorted({word for word in content if _stem(word) not in source_stems})
    if len(novel) > max(2, len(content) // 3):
        problems.append(f"adds content not in the cited evidence: {', '.join(novel)}")
    return problems


def _numbers(text: str) -> set[str]:
    return {number.replace(",", "") for number in re.findall(r"\d+(?:[.,]\d+)*", text)}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+(?:['-][a-z]+)*", text.casefold())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _has_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9#+]){re.escape(term)}(?![a-z0-9#+])", text) is not None


def _new_names(text: str, source: str) -> list[str]:
    """Capitalized tokens must appear in the evidence. An opening verb is exempt."""
    found: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9+#&./-]*", text):
        token = match.group(0).rstrip(".,;:").casefold()
        if not token:
            continue
        if not text[: match.start()].strip() and _opening_verb(token):
            continue
        singular = token[:-1] if token.endswith("s") and len(token) > 3 else token
        if not (_has_term(source, token) or _has_term(source, singular)):
            found.append(match.group(0).rstrip(".,;:"))
    return sorted(set(found))


def _opening_verb(token: str) -> bool:
    if not token.isalpha():
        return False
    return token in _REWORDING or token in _CLAIM_WORDS or token.endswith(("ed", "ing"))
