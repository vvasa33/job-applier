import math

import pytest
from pydantic import ValidationError

from jobhunter.apply.decisions import (
    AUTO_FILL_MIN_CONFIDENCE,
    LABEL_TRUST_MIN_CONFIDENCE,
    LOOSE_MATCH_CONFIDENCE,
    ApplicantData,
    DecisionSource,
    FieldAction,
    FieldDecision,
    KnownFact,
    decide,
)
from jobhunter.apply.fields import ApplicationField

JUST_BELOW_AUTO = math.nextafter(AUTO_FILL_MIN_CONFIDENCE, 0.0)
JUST_BELOW_LABEL = math.nextafter(LABEL_TRUST_MIN_CONFIDENCE, 0.0)


def _field(**overrides) -> ApplicationField:
    values = {
        "field_id": "email",
        "label": "Email",
        "type": "text",
        "options": (),
        "required": True,
        "current_value": "",
        "confidence": 0.95,
        "selector": "#email",
    }
    values.update(overrides)
    return ApplicationField(**values)


def _fact(key: str, value: str, confidence: float = 1.0, source: DecisionSource = DecisionSource.profile) -> KnownFact:
    return KnownFact(key=key, value=value, confidence=confidence, source=source)


def _data(*facts: KnownFact, **overrides) -> ApplicantData:
    return ApplicantData(facts=list(facts), **overrides)


def test_decision_schema_rejects_an_uncertain_auto_fill() -> None:
    with pytest.raises(ValidationError):
        FieldDecision(
            field_id="email",
            action=FieldAction.auto_fill,
            proposed_value="ada@example.com",
            confidence=JUST_BELOW_AUTO,
            reasoning="too low",
            source=DecisionSource.profile,
        )
    with pytest.raises(ValidationError):
        FieldDecision(
            field_id="email",
            action=FieldAction.auto_fill,
            proposed_value=None,
            confidence=0,
            reasoning="missing",
            source=DecisionSource.none,
        )
    with pytest.raises(ValidationError):
        FieldDecision(
            field_id="phone",
            action=FieldAction.skip,
            proposed_value="555",
            confidence=1,
            reasoning="invented",
            source=DecisionSource.profile,
        )
    with pytest.raises(ValidationError):
        FieldDecision(
            field_id="email",
            action=FieldAction.require_user,
            proposed_value=None,
            confidence=0.2,
            reasoning="no value",
            source=DecisionSource.none,
        )


def test_auto_fill_starts_at_the_confidence_boundary() -> None:
    assert LOOSE_MATCH_CONFIDENCE < AUTO_FILL_MIN_CONFIDENCE
    on_the_line = decide(
        _field(confidence=0.95),
        _data(_fact("contact.email", "ada@example.com", confidence=AUTO_FILL_MIN_CONFIDENCE)),
    )
    assert on_the_line.action == FieldAction.auto_fill
    assert on_the_line.proposed_value == "ada@example.com"
    assert on_the_line.confidence == AUTO_FILL_MIN_CONFIDENCE
    assert on_the_line.source == DecisionSource.profile
    assert on_the_line.canonical_key == "contact.email"

    field_on_the_line = decide(
        _field(confidence=AUTO_FILL_MIN_CONFIDENCE),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert field_on_the_line.action == FieldAction.auto_fill
    assert field_on_the_line.confidence == AUTO_FILL_MIN_CONFIDENCE


def test_confidence_just_below_the_boundary_is_not_entered() -> None:
    fact_low = decide(
        _field(),
        _data(_fact("contact.email", "ada@example.com", confidence=JUST_BELOW_AUTO)),
    )
    assert fact_low.action == FieldAction.require_user
    assert fact_low.proposed_value == "ada@example.com"
    assert fact_low.confidence == JUST_BELOW_AUTO
    assert fact_low.confidence < AUTO_FILL_MIN_CONFIDENCE
    assert "not entered automatically" in fact_low.reasoning

    field_low = decide(
        _field(confidence=JUST_BELOW_AUTO),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert field_low.action == FieldAction.require_user
    assert field_low.proposed_value == "ada@example.com"
    assert field_low.confidence == JUST_BELOW_AUTO


def test_label_trust_boundary_does_not_pour_a_fact_into_an_uncertain_field() -> None:
    trusted = decide(
        _field(field_id="custom-email", confidence=LABEL_TRUST_MIN_CONFIDENCE),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert trusted.canonical_key == "contact.email"
    assert trusted.action == FieldAction.require_user
    assert trusted.proposed_value == "ada@example.com"
    assert trusted.confidence == LABEL_TRUST_MIN_CONFIDENCE

    untrusted = decide(
        _field(field_id="custom-email", confidence=JUST_BELOW_LABEL, required=True),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert untrusted.canonical_key is None
    assert untrusted.action == FieldAction.require_user
    assert untrusted.proposed_value is None
    assert untrusted.confidence == 0

    optional = decide(
        _field(field_id="custom-email", confidence=JUST_BELOW_LABEL, required=False),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert optional.action == FieldAction.skip
    assert optional.proposed_value is None


def test_a_known_field_id_still_proposes_when_detection_confidence_is_low() -> None:
    decision = decide(
        _field(field_id="email", label="", confidence=0.4),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert decision.canonical_key == "contact.email"
    assert decision.action == FieldAction.require_user
    assert decision.proposed_value == "ada@example.com"
    assert decision.confidence == 0.4


def test_exact_option_match_is_automatic_and_a_near_match_is_not() -> None:
    exact = decide(
        _field(
            field_id="workAuthorization",
            label="Are you authorized to work in the United States?",
            type="radio",
            options=("Yes", "No"),
            required=True,
        ),
        _data(_fact("auth.work_authorized", "yes")),
    )
    assert exact.action == FieldAction.auto_fill
    assert exact.proposed_value == "Yes"
    assert exact.confidence == 0.95

    loose = decide(
        _field(
            field_id="auth-question",
            label="Are you legally authorized to work in the United States?",
            type="radio",
            options=("Yes, I am authorized to work in the United States",),
            confidence=1,
        ),
        _data(_fact("auth.work_authorized", "Yes", confidence=1)),
    )
    assert loose.action == FieldAction.require_user
    assert loose.proposed_value == "Yes, I am authorized to work in the United States"
    assert loose.confidence == LOOSE_MATCH_CONFIDENCE
    assert loose.confidence < AUTO_FILL_MIN_CONFIDENCE

    ambiguous = decide(
        _field(
            field_id="auth-question",
            label="Are you legally authorized to work in the United States?",
            type="radio",
            options=("Yes, I am authorized", "Yes, but I will need sponsorship"),
            confidence=1,
        ),
        _data(_fact("auth.work_authorized", "Yes", confidence=1)),
    )
    assert ambiguous.action == FieldAction.require_user
    assert ambiguous.proposed_value is None
    assert ambiguous.confidence == 0
    assert "more than one option" in ambiguous.reasoning.lower()


def test_work_authorization_and_sponsorship_are_not_guessed() -> None:
    authorization = decide(
        _field(
            field_id="workAuthorization",
            label="Work authorization",
            type="radio",
            options=("Yes", "No"),
            required=False,
        ),
        _data(),
    )
    assert authorization.action == FieldAction.require_user
    assert authorization.proposed_value is None
    assert authorization.confidence == 0
    assert "will not be guessed" in authorization.reasoning

    sponsorship = decide(
        _field(
            field_id="sponsor",
            label="Will you now or in the future require sponsorship?",
            type="radio",
            options=("Yes", "No"),
            required=False,
            confidence=1,
        ),
        _data(),
    )
    assert sponsorship.canonical_key == "auth.sponsorship"
    assert sponsorship.action == FieldAction.require_user
    assert sponsorship.proposed_value is None

    stored = decide(
        _field(field_id="sponsorship", label="Sponsorship", type="radio", options=("Yes", "No")),
        _data(_fact("auth.sponsorship", "No")),
    )
    assert stored.action == FieldAction.auto_fill
    assert stored.proposed_value == "No"


def test_attestations_salary_and_government_ids_are_never_automatic() -> None:
    attestation = decide(
        _field(field_id="agree", label="I certify that the information is true", type="checkbox", confidence=1),
        _data(_fact("legal.attestation", "yes", confidence=1)),
    )
    assert attestation.action == FieldAction.require_user
    assert attestation.proposed_value == "yes"
    assert attestation.confidence == 1
    assert attestation.action != FieldAction.auto_fill

    blank_attestation = decide(
        _field(field_id="agree", label="I agree to the terms and conditions", type="checkbox", required=False),
        _data(),
    )
    assert blank_attestation.action == FieldAction.require_user
    assert blank_attestation.proposed_value is None

    salary = decide(
        _field(field_id="pay", label="Salary expectation", type="text", required=False, confidence=1),
        _data(_fact("logistics.salary", "40", confidence=1)),
    )
    assert salary.action == FieldAction.require_user
    assert salary.proposed_value == "40"

    relocation = decide(
        _field(field_id="move", label="Are you willing to relocate?", type="radio", options=("Yes", "No"), confidence=1),
        _data(_fact("logistics.relocation", "Yes", confidence=1)),
    )
    assert relocation.action == FieldAction.require_user
    assert relocation.proposed_value == "Yes"

    identifier = decide(
        _field(field_id="ssn", label="Social Security Number", type="text", required=True),
        _data(_fact("legal.ssn", "123-45-6789", confidence=1)),
    )
    assert identifier.action == FieldAction.require_user
    assert identifier.proposed_value is None
    assert identifier.confidence == 0
    assert "123-45-6789" not in identifier.reasoning


def test_unknown_personal_information_is_asked_when_required_and_skipped_when_optional() -> None:
    missing_phone = decide(_field(field_id="phone", label="Phone", required=True), _data())
    assert missing_phone.action == FieldAction.require_user
    assert missing_phone.proposed_value is None
    assert "no value is stored" in missing_phone.reasoning

    optional_phone = decide(_field(field_id="phone", label="Phone", required=False), _data())
    assert optional_phone.action == FieldAction.skip
    assert optional_phone.proposed_value is None

    how_heard = decide(
        _field(
            field_id="hear",
            label="How did you hear about us?",
            type="free_text",
            required=False,
            confidence=0.7,
        ),
        _data(resume_text="Built tools in Python.", job_title="Intern", company="Northwind"),
    )
    assert how_heard.canonical_key == "source.how_heard"
    assert how_heard.action == FieldAction.skip

    required_how_heard = decide(
        _field(field_id="hear", label="How did you hear about us?", type="free_text", required=True, confidence=0.7),
        _data(),
    )
    assert required_how_heard.action == FieldAction.require_user
    assert required_how_heard.proposed_value is None


def test_free_text_is_suggested_only_from_real_material() -> None:
    draftable = decide(
        _field(
            field_id="question-123",
            label="Why do you want this internship?",
            type="free_text",
            required=True,
        ),
        _data(resume_text="Built a compiler.", job_title="Software Engineering Intern", company="Northwind"),
    )
    assert draftable.action == FieldAction.suggest_and_ask
    assert draftable.proposed_value is None
    assert draftable.confidence == 0
    assert draftable.source == DecisionSource.resume
    assert "No draft is stored" in draftable.reasoning
    assert "compiler" not in draftable.reasoning

    job_only = decide(
        _field(field_id="why", label="Why are you interested in this role?", type="free_text"),
        _data(job_description="You will write Python services."),
    )
    assert job_only.action == FieldAction.suggest_and_ask
    assert job_only.proposed_value is None
    assert job_only.source == DecisionSource.job

    stored = decide(
        _field(field_id="why", label="Why do you want this internship?", type="free_text", confidence=0.99),
        _data(_fact("essay.why", "I want the compiler work described in the posting.", confidence=0.99)),
    )
    assert stored.action == FieldAction.suggest_and_ask
    assert stored.proposed_value == "I want the compiler work described in the posting."
    assert stored.confidence == 0.99
    assert stored.action != FieldAction.auto_fill

    low_draft = decide(
        _field(field_id="why", label="Why do you want this internship?", type="free_text", confidence=0.95),
        _data(_fact("essay.why", "Stored note.", confidence=0.5)),
    )
    assert low_draft.action == FieldAction.suggest_and_ask
    assert low_draft.confidence == 0.5

    nothing = decide(
        _field(field_id="why", label="Describe a project you are proud of", type="free_text", required=False),
        _data(),
    )
    assert nothing.action == FieldAction.require_user
    assert nothing.proposed_value is None
    assert nothing.confidence == 0


def test_more_specific_stored_answers_win_without_blending() -> None:
    decision = decide(
        _field(field_id="sponsorship", type="radio", options=("Yes", "No"), confidence=1),
        _data(
            _fact("auth.sponsorship", "Yes", confidence=0.99, source=DecisionSource.profile),
            _fact("auth.sponsorship", "No", confidence=0.91, source=DecisionSource.answer_bank),
        ),
    )
    assert decision.action == FieldAction.auto_fill
    assert decision.proposed_value == "No"
    assert decision.source == DecisionSource.answer_bank
    assert decision.confidence == 0.91

    application = decide(
        _field(field_id="sponsorship", type="radio", options=("Yes", "No"), confidence=1),
        _data(
            _fact("auth.sponsorship", "No", confidence=1, source=DecisionSource.answer_bank),
            _fact("auth.sponsorship", "Yes", confidence=0.95, source=DecisionSource.application),
        ),
    )
    assert application.proposed_value == "Yes"
    assert application.source == DecisionSource.application
    assert application.confidence == 0.95


def test_blank_facts_and_unmatched_options_do_not_become_answers() -> None:
    blank = decide(_field(), _data(_fact("contact.email", "   ")))
    assert blank.action == FieldAction.require_user
    assert blank.proposed_value is None

    country = decide(
        _field(field_id="country", label="Country", type="dropdown", options=("United States", "Canada"), confidence=1),
        _data(_fact("contact.country", "united states")),
    )
    assert country.action == FieldAction.auto_fill
    assert country.proposed_value == "United States"

    unknown_country = decide(
        _field(field_id="country", label="Country", type="dropdown", options=("United States", "Canada")),
        _data(_fact("contact.country", "USA")),
    )
    assert unknown_country.action == FieldAction.require_user
    assert unknown_country.proposed_value is None
    assert "does not match" in unknown_country.reasoning

    degree = decide(
        _field(field_id="degree", label="Degree", type="dropdown", options=("Bachelors", "Masters")),
        _data(_fact("edu.degree", "BS")),
    )
    assert degree.proposed_value is None
    assert degree.action == FieldAction.require_user


def test_checkbox_employment_and_eeo_need_an_explicit_answer() -> None:
    missing = decide(
        _field(
            field_id="previouslyEmployed",
            label="I have previously worked here",
            type="checkbox",
            required=False,
        ),
        _data(),
    )
    assert missing.action == FieldAction.require_user
    assert missing.proposed_value is None

    employed = decide(
        _field(field_id="previouslyEmployed", label="I have previously worked here", type="checkbox"),
        _data(_fact("employment.previously_employed", "yes")),
    )
    assert employed.action == FieldAction.auto_fill
    assert employed.proposed_value == "yes"

    vague = decide(
        _field(field_id="previouslyEmployed", type="checkbox", confidence=1),
        _data(_fact("employment.previously_employed", "maybe", confidence=1)),
    )
    assert vague.action == FieldAction.require_user
    assert vague.proposed_value is None

    gender = decide(
        _field(field_id="gender", label="Gender", type="dropdown", options=("Decline to self-identify", "Woman"), required=False),
        _data(),
    )
    assert gender.action == FieldAction.require_user
    assert gender.proposed_value is None

    disclosed = decide(
        _field(
            field_id="gender",
            label="Gender",
            type="dropdown",
            options=("Decline to self-identify", "Woman"),
            confidence=1,
        ),
        _data(_fact("eeo.gender", "Decline to self-identify")),
    )
    assert disclosed.action == FieldAction.auto_fill
    assert disclosed.proposed_value == "Decline to self-identify"


def test_files_use_a_stored_path_and_do_not_invent_one() -> None:
    resume = decide(
        _field(field_id="file-upload-input-ref", label="Resume/CV", type="file", confidence=0.95),
        _data(resume_path="/tmp/resume.pdf"),
    )
    assert resume.action == FieldAction.auto_fill
    assert resume.proposed_value == "/tmp/resume.pdf"
    assert resume.source == DecisionSource.resume
    assert resume.confidence == 0.95

    uncertain_file = decide(
        _field(field_id="file-upload-input-ref", label="Resume/CV", type="file", confidence=JUST_BELOW_AUTO),
        _data(resume_path="/tmp/resume.pdf"),
    )
    assert uncertain_file.action == FieldAction.require_user
    assert uncertain_file.proposed_value == "/tmp/resume.pdf"
    assert uncertain_file.confidence == JUST_BELOW_AUTO

    missing_resume = decide(
        _field(field_id="upload", label="Resume", type="file", required=True),
        _data(),
    )
    assert missing_resume.action == FieldAction.require_user
    assert missing_resume.proposed_value is None

    optional_cover = decide(
        _field(field_id="cover", label="Cover letter", type="file", required=False),
        _data(resume_path="/tmp/resume.pdf"),
    )
    assert optional_cover.action == FieldAction.skip
    assert optional_cover.proposed_value is None

    stored_cover = decide(
        _field(field_id="cover", label="Cover letter", type="file", confidence=1),
        _data(_fact("file.cover_letter", "/tmp/cover.pdf", confidence=1)),
    )
    assert stored_cover.action == FieldAction.suggest_and_ask
    assert stored_cover.proposed_value == "/tmp/cover.pdf"
    assert stored_cover.action != FieldAction.auto_fill


def test_a_different_current_value_blocks_automatic_entry() -> None:
    conflict = decide(
        _field(current_value="grace@example.com"),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert conflict.action == FieldAction.require_user
    assert conflict.proposed_value == "ada@example.com"
    assert conflict.confidence == 0.95
    assert "different value" in conflict.reasoning

    same = decide(
        _field(current_value="Ada@Example.com"),
        _data(_fact("contact.email", "ada@example.com")),
    )
    assert same.action == FieldAction.auto_fill
    assert same.proposed_value == "ada@example.com"


def test_unrecognized_fields_are_not_filled() -> None:
    required = decide(_field(field_id="mystery", label="", type="text", required=True, confidence=0.4), _data())
    assert required.action == FieldAction.require_user
    assert required.proposed_value is None

    optional = decide(_field(field_id="mystery", label="", type="text", required=False, confidence=0.4), _data())
    assert optional.action == FieldAction.skip
    assert optional.confidence == 0


def test_loose_match_uses_the_lower_of_field_fact_and_fit_confidence() -> None:
    decision = decide(
        _field(
            field_id="auth-question",
            label="Are you legally authorized to work in the United States?",
            type="radio",
            options=("Yes, I am authorized to work in the United States",),
            confidence=0.8,
        ),
        _data(_fact("auth.work_authorized", "Yes", confidence=0.99)),
    )
    assert decision.action == FieldAction.require_user
    assert decision.confidence == 0.8
    assert decision.proposed_value == "Yes, I am authorized to work in the United States"


def test_equal_confidence_facts_from_the_same_source_keep_the_first() -> None:
    decision = decide(
        _field(confidence=1),
        _data(
            _fact("contact.email", "first@example.com", confidence=1),
            _fact("contact.email", "second@example.com", confidence=1),
        ),
    )
    assert decision.proposed_value == "first@example.com"
    assert decision.action == FieldAction.auto_fill
