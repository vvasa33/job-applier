"""Decide what to do with one application field. It never invents an answer."""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobhunter.apply.fields import ApplicationField

AUTO_FILL_MIN_CONFIDENCE = 0.9
LOOSE_MATCH_CONFIDENCE = 0.85
LABEL_TRUST_MIN_CONFIDENCE = 0.5

_SOURCE_PRIORITY = {"application": 0, "answer_bank": 1, "profile": 2, "resume": 3, "job": 4, "none": 5}
_YES = frozenset({"yes", "true"})
_NO = frozenset({"no", "false"})
_FILE_KEYS = frozenset({"file.resume", "file.cover_letter", "file.transcript"})
_ESSAY_KEYS = frozenset({"essay.why", "essay.other"})
_ALWAYS_ASK = frozenset({
    "legal.attestation",
    "legal.criminal",
    "legal.ssn",
    "logistics.salary",
    "logistics.relocation",
})
_CONSEQUENTIAL = frozenset({
    "auth.work_authorized",
    "auth.sponsorship",
    "auth.citizenship",
    "auth.clearance",
    "eeo.gender",
    "eeo.race",
    "eeo.veteran",
    "eeo.disability",
    "employment.previously_employed",
    "logistics.start_date",
    *_ALWAYS_ASK,
})
_GATED_KEYS = frozenset({
    "contact.first_name",
    "contact.last_name",
    "contact.email",
    "contact.phone",
    "contact.address",
    "contact.city",
    "contact.country",
    "contact.postal_code",
    "contact.linkedin",
    "edu.school",
    "edu.degree",
    "edu.major",
    "edu.gpa",
    "edu.graduation",
    "source.how_heard",
    *_FILE_KEYS,
})
_FIELD_IDS = {
    "legalName--firstName": "contact.first_name",
    "legalName--lastName": "contact.last_name",
    "email": "contact.email",
    "phone": "contact.phone",
    "addressLine1": "contact.address",
    "city": "contact.city",
    "country": "contact.country",
    "postalCode": "contact.postal_code",
    "school": "edu.school",
    "degree": "edu.degree",
    "workAuthorization": "auth.work_authorized",
    "sponsorship": "auth.sponsorship",
    "previouslyEmployed": "employment.previously_employed",
}
_LABEL_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"social security|\bssn\b|passport|driver'?s license|government id", re.I), "legal.ssn"),
    (re.compile(r"\bi agree\b|\bi certify\b|\bi acknowledge\b|attestation|\bsignature\b|perjury|terms and conditions", re.I), "legal.attestation"),
    (re.compile(r"criminal|felony|convicted", re.I), "legal.criminal"),
    (re.compile(r"sponsor", re.I), "auth.sponsorship"),
    (re.compile(r"clearance", re.I), "auth.clearance"),
    (re.compile(r"citizen", re.I), "auth.citizenship"),
    (re.compile(r"authori[sz]ed to work|work authori[sz]ation|legally authori[sz]ed|eligible to work", re.I), "auth.work_authorized"),
    (re.compile(r"salary|compensation|desired pay|pay expectation", re.I), "logistics.salary"),
    (re.compile(r"relocat", re.I), "logistics.relocation"),
    (re.compile(r"start date|available to start|earliest start", re.I), "logistics.start_date"),
    (re.compile(r"previously (worked|employed)|worked here|former employee", re.I), "employment.previously_employed"),
    (re.compile(r"how did you hear|where did you hear", re.I), "source.how_heard"),
    (re.compile(r"why do you|why are you|what interests you|tell us why", re.I), "essay.why"),
    (re.compile(r"\bdescribe\b|tell us about|anything else", re.I), "essay.other"),
    (re.compile(r"cover letter", re.I), "file.cover_letter"),
    (re.compile(r"transcript", re.I), "file.transcript"),
    (re.compile(r"\bresume\b|\bcv\b", re.I), "file.resume"),
    (re.compile(r"\bgender\b", re.I), "eeo.gender"),
    (re.compile(r"\brace\b|ethnicity", re.I), "eeo.race"),
    (re.compile(r"veteran", re.I), "eeo.veteran"),
    (re.compile(r"disabilit", re.I), "eeo.disability"),
    (re.compile(r"\bgpa\b|grade point", re.I), "edu.gpa"),
    (re.compile(r"field of study|\bmajor\b", re.I), "edu.major"),
    (re.compile(r"\bdegree\b", re.I), "edu.degree"),
    (re.compile(r"university|college|\bschool\b", re.I), "edu.school"),
    (re.compile(r"graduation|grad date", re.I), "edu.graduation"),
    (re.compile(r"first name|given name", re.I), "contact.first_name"),
    (re.compile(r"last name|family name|surname", re.I), "contact.last_name"),
    (re.compile(r"e-?mail", re.I), "contact.email"),
    (re.compile(r"phone|mobile", re.I), "contact.phone"),
    (re.compile(r"linkedin", re.I), "contact.linkedin"),
    (re.compile(r"postal|zip code", re.I), "contact.postal_code"),
    (re.compile(r"\bcountry\b", re.I), "contact.country"),
    (re.compile(r"\bcity\b", re.I), "contact.city"),
    (re.compile(r"address", re.I), "contact.address"),
)
_NAMES = {
    "contact.first_name": "First name",
    "contact.last_name": "Last name",
    "contact.email": "Email",
    "contact.phone": "Phone number",
    "contact.address": "Address",
    "contact.city": "City",
    "contact.country": "Country",
    "contact.postal_code": "Postal code",
    "contact.linkedin": "LinkedIn URL",
    "edu.school": "School",
    "edu.degree": "Degree",
    "edu.major": "Major",
    "edu.gpa": "GPA",
    "edu.graduation": "Graduation date",
    "auth.work_authorized": "Work authorization",
    "auth.sponsorship": "Sponsorship",
    "auth.citizenship": "Citizenship",
    "auth.clearance": "Security clearance",
    "eeo.gender": "Gender",
    "eeo.race": "Race or ethnicity",
    "eeo.veteran": "Veteran status",
    "eeo.disability": "Disability status",
    "employment.previously_employed": "Previous employment at this company",
    "logistics.start_date": "Start date",
    "logistics.salary": "Salary expectation",
    "logistics.relocation": "Relocation",
    "legal.attestation": "Attestation",
    "legal.criminal": "Criminal-history question",
    "legal.ssn": "Government identifier",
    "source.how_heard": "How you heard about this role",
    "essay.why": "Free-text question",
    "essay.other": "Free-text question",
    "file.resume": "Resume file",
    "file.cover_letter": "Cover letter",
    "file.transcript": "Transcript",
}


class FieldAction(StrEnum):
    auto_fill = "auto_fill"
    suggest_and_ask = "suggest_and_ask"
    require_user = "require_user"
    skip = "skip"


class DecisionSource(StrEnum):
    profile = "profile"
    answer_bank = "answer_bank"
    application = "application"
    resume = "resume"
    job = "job"
    none = "none"


class KnownFact(BaseModel):
    """One value the user has already stored. Confidence is how sure that stored value is."""

    model_config = ConfigDict(extra="forbid")

    key: str
    value: str
    confidence: float = Field(ge=0, le=1)
    source: DecisionSource


class ApplicantData(BaseModel):
    """Profile facts, saved answers, and the materials a free-text draft could cite."""

    model_config = ConfigDict(extra="forbid")

    facts: list[KnownFact] = Field(default_factory=list)
    resume_path: str | None = None
    resume_text: str | None = None
    job_title: str | None = None
    company: str | None = None
    job_description: str | None = None


class FieldDecision(BaseModel):
    """What to do with one field. Confidence is confidence in proposed_value, and it is 0 when nothing is proposed."""

    model_config = ConfigDict(extra="forbid")

    field_id: str
    action: FieldAction
    proposed_value: str | None = None
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    source: DecisionSource
    canonical_key: str | None = None

    @model_validator(mode="after")
    def check_action(self) -> "FieldDecision":
        has_value = self.proposed_value is not None
        if has_value and not self.proposed_value.strip():
            raise ValueError("proposed value is empty")
        if not has_value and self.confidence != 0:
            raise ValueError("a decision with no proposed value has confidence 0")
        if self.action == FieldAction.auto_fill:
            if not has_value:
                raise ValueError("auto-fill requires a proposed value")
            if self.confidence < AUTO_FILL_MIN_CONFIDENCE:
                raise ValueError("auto-fill requires high confidence")
            if self.source == DecisionSource.none:
                raise ValueError("auto-fill requires a source")
        if self.action == FieldAction.skip and has_value:
            raise ValueError("skip does not propose a value")
        return self


def is_consequential(key: str | None) -> bool:
    return key in _CONSEQUENTIAL


def decide(field: ApplicationField, data: ApplicantData) -> FieldDecision:
    """Classify one field. Stored facts are repeated, never completed or guessed."""

    key = _canonical_key(field)
    if key == "legal.ssn":
        return _decision(
            field,
            key,
            FieldAction.require_user,
            None,
            0,
            DecisionSource.none,
            "Government identifiers are never filled from stored data.",
        )
    if key in _ESSAY_KEYS:
        return _essay(field, data, key)

    fact = _lookup(data, key) if key else None
    if key in _ALWAYS_ASK:
        return _confirm(field, key, fact)
    if key == "file.resume":
        return _resume_file(field, data, fact)
    if key == "file.cover_letter":
        return _cover_letter(field, fact)
    if fact is None:
        return _missing(field, key)
    return _known(field, key, fact)


def _essay(field: ApplicationField, data: ApplicantData, key: str) -> FieldDecision:
    fact = _lookup(data, key)
    if fact is not None:
        confidence = min(field.confidence, fact.confidence)
        return _decision(
            field,
            key,
            FieldAction.suggest_and_ask,
            fact.value,
            confidence,
            fact.source,
            "A stored draft is available for this free-text question. It is not entered until you approve it.",
        )
    materials: list[str] = []
    source = DecisionSource.none
    if data.resume_text and data.resume_text.strip():
        materials.append("the resume")
        source = DecisionSource.resume
    if (data.job_description and data.job_description.strip()) or (data.job_title and data.job_title.strip()):
        materials.append("the job")
        if source == DecisionSource.none:
            source = DecisionSource.job
    if materials:
        joined = " and ".join(materials)
        return _decision(
            field,
            key,
            FieldAction.suggest_and_ask,
            None,
            0,
            source,
            f"This free-text question can be drafted from {joined}. No draft is stored, so none is proposed.",
        )
    return _decision(
        field,
        key,
        FieldAction.require_user,
        None,
        0,
        DecisionSource.none,
        "This free-text question has no stored draft and no resume or job text to draft from.",
    )


def _confirm(field: ApplicationField, key: str, fact: KnownFact | None) -> FieldDecision:
    name = _NAMES.get(key, "This question")
    if fact is None:
        return _decision(
            field,
            key,
            FieldAction.require_user,
            None,
            0,
            DecisionSource.none,
            f"{name} is never answered automatically, and no explicit answer is stored.",
        )
    proposed, fit_confidence, fit_reason = _fit(field, fact.value)
    if proposed is None:
        return _decision(
            field,
            key,
            FieldAction.require_user,
            None,
            0,
            fact.source,
            f"{name} is never answered automatically. {fit_reason}",
        )
    confidence = min(field.confidence, fact.confidence, fit_confidence)
    return _decision(
        field,
        key,
        FieldAction.require_user,
        proposed,
        confidence,
        fact.source,
        f"{name} is never entered automatically. {fit_reason}",
    )


def _resume_file(field: ApplicationField, data: ApplicantData, fact: KnownFact | None) -> FieldDecision:
    if fact is None and data.resume_path and data.resume_path.strip():
        fact = KnownFact(key="file.resume", value=data.resume_path, confidence=1, source=DecisionSource.resume)
    if fact is None:
        return _missing(field, "file.resume")
    return _known(field, "file.resume", fact)


def _cover_letter(field: ApplicationField, fact: KnownFact | None) -> FieldDecision:
    if fact is None:
        if field.required:
            return _decision(
                field,
                "file.cover_letter",
                FieldAction.require_user,
                None,
                0,
                DecisionSource.none,
                "A cover letter is required and none is stored.",
            )
        return _decision(
            field,
            "file.cover_letter",
            FieldAction.skip,
            None,
            0,
            DecisionSource.none,
            "The cover letter is optional and none is stored, so it is left blank.",
        )
    confidence = min(field.confidence, fact.confidence)
    return _decision(
        field,
        "file.cover_letter",
        FieldAction.suggest_and_ask,
        fact.value,
        confidence,
        fact.source,
        "A cover letter file is stored. It is not uploaded until you approve it.",
    )


def _missing(field: ApplicationField, key: str | None) -> FieldDecision:
    name = _NAMES.get(key or "", "This field")
    if key in _CONSEQUENTIAL:
        return _decision(
            field,
            key,
            FieldAction.require_user,
            None,
            0,
            DecisionSource.none,
            f"{name} has no explicit stored answer, and it will not be guessed.",
        )
    if field.required:
        if key is None:
            reason = "This required field is not recognized, so nothing is entered."
        else:
            reason = f"{name} is required and no value is stored."
        return _decision(field, key, FieldAction.require_user, None, 0, DecisionSource.none, reason)
    if key is None:
        reason = "This optional field is not recognized and has no stored value, so it is left blank."
    else:
        reason = f"{name} is optional and no value is stored, so it is left blank."
    return _decision(field, key, FieldAction.skip, None, 0, DecisionSource.none, reason)


def _known(field: ApplicationField, key: str, fact: KnownFact) -> FieldDecision:
    proposed, fit_confidence, fit_reason = _fit(field, fact.value)
    name = _NAMES.get(key, "This value")
    if proposed is None:
        return _decision(
            field,
            key,
            FieldAction.require_user,
            None,
            0,
            fact.source,
            f"{name} is stored, but {fit_reason[0].lower()}{fit_reason[1:]}",
        )
    confidence = min(field.confidence, fact.confidence, fit_confidence)
    if _conflicts(field, proposed):
        return _decision(
            field,
            key,
            FieldAction.require_user,
            proposed,
            confidence,
            fact.source,
            f"{name} is stored, but the field already contains a different value. {fit_reason}",
        )
    if confidence >= AUTO_FILL_MIN_CONFIDENCE:
        return _decision(
            field,
            key,
            FieldAction.auto_fill,
            proposed,
            confidence,
            fact.source,
            f"{name} is stored with high confidence. {fit_reason}",
        )
    shown = f"{confidence:.3f}"
    floor = f"{AUTO_FILL_MIN_CONFIDENCE:.3f}"
    return _decision(
        field,
        key,
        FieldAction.require_user,
        proposed,
        confidence,
        fact.source,
        f"Confidence {shown} is below {floor}, so the stored value is not entered automatically. {fit_reason}",
    )


def _decision(
    field: ApplicationField,
    key: str | None,
    action: FieldAction,
    proposed: str | None,
    confidence: float,
    source: DecisionSource,
    reasoning: str,
) -> FieldDecision:
    return FieldDecision(
        field_id=field.field_id,
        canonical_key=key,
        action=action,
        proposed_value=proposed,
        confidence=confidence,
        reasoning=reasoning,
        source=source,
    )


def _canonical_key(field: ApplicationField) -> str | None:
    mapped = _FIELD_IDS.get(field.field_id)
    if mapped:
        return mapped
    label_key = _key_from_label(field.label, field.type)
    if label_key is None:
        return None
    if label_key in _GATED_KEYS and field.confidence < LABEL_TRUST_MIN_CONFIDENCE:
        return None
    return label_key


def _key_from_label(label: str, field_type: str) -> str | None:
    if not label.strip():
        return None
    for pattern, key in _LABEL_RULES:
        if pattern.search(label):
            if key in _FILE_KEYS and field_type != "file":
                if key == "file.cover_letter":
                    return "essay.other"
                return None
            return key
    return None


def _lookup(data: ApplicantData, key: str | None) -> KnownFact | None:
    if key is None:
        return None
    matches = [fact for fact in data.facts if fact.key == key and fact.value.strip()]
    if not matches:
        return None
    matches.sort(key=lambda fact: (_SOURCE_PRIORITY[fact.source.value], -fact.confidence))
    return matches[0]


def _fit(field: ApplicationField, value: str) -> tuple[str | None, float, str]:
    if field.type == "checkbox":
        return _fit_checkbox(value)
    if not field.options:
        return value, 1.0, "The stored value is used as entered."
    normalized = _normalize(value)
    exact = [option for option in field.options if _normalize(option) == normalized]
    if len(exact) == 1:
        return exact[0], 1.0, "The stored value matches one option."
    if len(exact) > 1:
        return None, 0.0, "More than one option matches the stored value."
    head = normalized.split()[:1]
    if head and head[0] in _YES | _NO:
        loose = [option for option in field.options if _normalize(option).split()[:1] == head]
        if len(loose) == 1:
            return loose[0], LOOSE_MATCH_CONFIDENCE, "The stored answer matches the start of exactly one option."
        if len(loose) > 1:
            return None, 0.0, "More than one option starts with the stored answer."
    return None, 0.0, "The stored value does not match the options."


def _fit_checkbox(value: str) -> tuple[str | None, float, str]:
    normalized = _normalize(value)
    if normalized in _YES:
        return "yes", 1.0, "The stored answer is yes."
    if normalized in _NO:
        return "no", 1.0, "The stored answer is no."
    return None, 0.0, "The stored value is not an explicit yes or no."


def _conflicts(field: ApplicationField, proposed: str) -> bool:
    current = field.current_value.strip()
    if not current:
        return False
    if field.type == "checkbox":
        checked = _normalize(current) in _YES | {"checked"}
        return checked != (proposed == "yes")
    return _normalize(current) != _normalize(proposed)


def _normalize(text: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in text)
    return " ".join(cleaned.split())
