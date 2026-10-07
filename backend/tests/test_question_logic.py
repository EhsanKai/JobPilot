"""V0.9 question logic without a database: recognizing deterministic topics,
the deterministic answers (including every "needs your input" case), and the
truth validation of AI answers. Pure functions; no AI, no network."""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.application import NoticePeriodUnit, WorkAuthorization
from app.models.application_question import QuestionType as T
from app.schemas.question_ai import AIQuestionAnswer
from app.services.documents.context import Preparation
from app.services.questions import deterministic, prompts
from app.services.questions.classify import Intent, classify
from app.services.questions.context import build_question_context
from app.services.questions.deterministic import (
    NeedsInput,
    QuestionShape,
    Suggested,
    known_data,
    resolve,
)
from app.services.questions.text import normalize_question, question_hash
from app.services.questions.validation import validate_answer
from tests.document_helpers import accepted_profile_data, context_job
from tests.question_helpers import fact_with, needs_input, option_answer, text_answer

PROFILE_ID = "00000000-0000-4000-8000-00000000e001"


# --- Normalization ----------------------------------------------------------------------------


def test_normalized_text_ignores_case_whitespace_and_trailing_markers() -> None:
    assert normalize_question("  Why   do you want to join us?*  ") == "why do you want to join us"
    assert question_hash("Salary?") == question_hash(" salary ")
    assert question_hash("Salary?") != question_hash("Notice period?")


def test_normalized_text_keeps_diacritics() -> None:
    assert normalize_question("Kündigungsfrist?") == "kündigungsfrist"


# --- Which questions are deterministic --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "question_type", "intent"),
    [
        ("What are your salary expectations?", T.FREE_TEXT, Intent.SALARY),
        ("Gehaltsvorstellung", T.FREE_TEXT, Intent.SALARY),
        ("Desired annual salary (EUR)", T.NUMERIC, Intent.SALARY),
        ("Anything at all", T.SALARY, Intent.SALARY),
        ("When can you start?", T.FREE_TEXT, Intent.START_DATE),
        ("Earliest possible start date", T.DATE, Intent.START_DATE),
        ("Ab wann sind Sie verfügbar?", T.FREE_TEXT, Intent.START_DATE),
        ("What is your notice period?", T.FREE_TEXT, Intent.NOTICE_PERIOD),
        ("Kündigungsfrist", T.FREE_TEXT, Intent.NOTICE_PERIOD),
        ("Are you authorized to work in Germany?", T.YES_NO, Intent.WORK_AUTHORIZATION),
        ("Do you have a work permit?", T.YES_NO, Intent.WORK_AUTHORIZATION),
        ("Do you require visa sponsorship?", T.YES_NO, Intent.SPONSORSHIP),
        ("Preferred language of communication", T.FREE_TEXT, Intent.APPLICATION_LANGUAGE),
        ("How many years of experience do you have?", T.NUMERIC, Intent.TOTAL_EXPERIENCE),
        ("Email", T.FREE_TEXT, Intent.EMAIL),
        ("Phone number", T.FREE_TEXT, Intent.PHONE),
        ("LinkedIn profile", T.FREE_TEXT, Intent.LINKEDIN),
        ("Full name", T.FREE_TEXT, Intent.FULL_NAME),
        ("Current location", T.FREE_TEXT, Intent.LOCATION),
    ],
)
def test_structured_topics_are_recognized(text: str, question_type: T, intent: Intent) -> None:
    assert classify(text, question_type) is intent


@pytest.mark.parametrize(
    "text",
    [
        "Why do you want to work for our company?",
        "Why are you interested in this role? Our salary is competitive.",
        "How many years of Java experience do you have?",
        "Do you have a driving license?",
        "What languages do you speak?",
        "Can we contact you by phone?",
        "Describe your notice period and why it is that long",
        "Tell us about yourself",
        "x" * 300 + " salary expectations",
    ],
)
def test_open_ended_and_factual_questions_are_not_deterministic(text: str) -> None:
    assert classify(text, T.FREE_TEXT) is None


# --- Deterministic answers --------------------------------------------------------------------


def shape(
    text: str = "",
    question_type: T = T.FREE_TEXT,
    max_length: int | None = None,
    options: list[str] | None = None,
) -> QuestionShape:
    return QuestionShape(text, question_type, max_length, options)


def prep(**values: Any) -> Preparation:
    base: dict[str, Any] = {"language": "de"}
    base.update(values)
    return Preparation(**base)


def answer(intent: Intent, question: QuestionShape, preparation: Preparation, **kw: Any) -> Any:
    data = known_data(preparation, **kw)
    return resolve(intent, question, data)


def confirmed() -> dict[str, Any]:
    return {"profile_id": PROFILE_ID, "profile_data": accepted_profile_data()}


def test_salary_uses_the_applications_figure_in_the_application_language() -> None:
    p = prep(salary_expectation=Decimal("78000"), salary_currency="EUR")
    de = answer(Intent.SALARY, shape("Gehalt", T.FREE_TEXT), p)
    assert isinstance(de, Suggested) and de.text == "78.000 EUR brutto pro Jahr"
    assert de.fact_ids == ["app.salary_expectation"]
    en = answer(
        Intent.SALARY, shape("Salary", T.FREE_TEXT), prep(**{**p.__dict__, "language": "en"})
    )
    assert isinstance(en, Suggested) and en.text == "78,000 EUR gross per year"
    num = answer(Intent.SALARY, shape("Salary", T.NUMERIC), p)
    assert isinstance(num, Suggested) and num.text == "78000"


def test_salary_with_cents_is_not_rounded() -> None:
    p = prep(salary_expectation=Decimal("78000.50"), salary_currency="EUR")
    result = answer(Intent.SALARY, shape("Salary", T.NUMERIC), p)
    assert isinstance(result, Suggested) and result.text == "78000.50"


def test_salary_missing_needs_input_never_zero() -> None:
    result = answer(Intent.SALARY, shape("Salary", T.NUMERIC), prep())
    assert result == NeedsInput("SALARY_MISSING")


def test_salary_is_never_mapped_to_a_band() -> None:
    p = prep(salary_expectation=Decimal("78000"), salary_currency="EUR")
    question = shape("Salary", T.SINGLE_CHOICE, options=["60-70k", "70-80k", "80-90k"])
    assert answer(Intent.SALARY, question, p) == NeedsInput("OPTION_NOT_MATCHED")


def test_a_character_limit_is_respected_never_truncated() -> None:
    p = prep(salary_expectation=Decimal("78000"), salary_currency="EUR")
    fits = answer(Intent.SALARY, shape("Salary", max_length=6), p)
    assert isinstance(fits, Suggested) and fits.text == "78.000"
    assert answer(Intent.SALARY, shape("Salary", max_length=5), p) == NeedsInput("ANSWER_TOO_LONG")


def test_start_date_formats_for_the_type() -> None:
    p = prep(available_from=date(2027, 1, 1))
    text = answer(Intent.START_DATE, shape("When can you start?"), p)
    assert isinstance(text, Suggested) and text.text == "Verfügbar ab 01.01.2027"
    iso = answer(Intent.START_DATE, shape("Start date", T.DATE), p)
    assert isinstance(iso, Suggested) and iso.text == "2027-01-01"
    english = answer(
        Intent.START_DATE, shape("Start"), prep(language="en", available_from=date(2027, 1, 1))
    )
    assert isinstance(english, Suggested) and english.text == "Available from 2027-01-01"


def test_no_date_is_calculated_from_a_notice_period() -> None:
    p = prep(notice_period=(3, NoticePeriodUnit.MONTHS))
    assert answer(Intent.START_DATE, shape("Start date", T.DATE), p) == NeedsInput(
        "DATE_NOT_STATED"
    )
    text = answer(Intent.START_DATE, shape("When can you start?"), p)
    assert isinstance(text, Suggested) and text.text == "Kündigungsfrist: 3 Monate"


def test_start_date_missing() -> None:
    assert answer(Intent.START_DATE, shape("Start"), prep()) == NeedsInput("START_DATE_MISSING")


def test_notice_period_text_numbers_and_options() -> None:
    p = prep(notice_period=(3, NoticePeriodUnit.MONTHS))
    text = answer(Intent.NOTICE_PERIOD, shape("Notice"), p)
    assert isinstance(text, Suggested) and text.text == "3 Monate"
    month = answer(Intent.NOTICE_PERIOD, shape("Notice period (in months)", T.NUMERIC), p)
    assert isinstance(month, Suggested) and month.text == "3"
    # no unit asked: ambiguous, never guessed
    assert answer(Intent.NOTICE_PERIOD, shape("Notice period", T.NUMERIC), p) == NeedsInput(
        "UNIT_MISMATCH"
    )
    # months are not converted into weeks
    assert answer(
        Intent.NOTICE_PERIOD, shape("Notice period in weeks", T.NUMERIC), p
    ) == NeedsInput("UNIT_MISMATCH")
    chosen = answer(
        Intent.NOTICE_PERIOD,
        shape("Notice", T.SINGLE_CHOICE, options=["1 month", "3 months", "6 months"]),
        p,
    )
    assert isinstance(chosen, Suggested) and chosen.options == ["3 months"]
    assert answer(
        Intent.NOTICE_PERIOD, shape("Notice", T.SINGLE_CHOICE, options=["1 month", "6 months"]), p
    ) == NeedsInput("OPTION_NOT_MATCHED")


def test_weeks_convert_to_days_exactly() -> None:
    p = prep(notice_period=(2, NoticePeriodUnit.WEEKS))
    result = answer(Intent.NOTICE_PERIOD, shape("Notice period in days", T.NUMERIC), p)
    assert isinstance(result, Suggested) and result.text == "14"


def test_immediate_availability() -> None:
    p = prep(notice_period=(0, NoticePeriodUnit.DAYS))
    result = answer(Intent.NOTICE_PERIOD, shape("Notice"), p)
    assert isinstance(result, Suggested) and result.text == "Sofort verfügbar"


def test_work_authorization_yes_no_and_options() -> None:
    p = prep(work_authorization=WorkAuthorization.AUTHORIZED)
    yes = answer(Intent.WORK_AUTHORIZATION, shape("Authorized to work?", T.YES_NO), p)
    assert isinstance(yes, Suggested) and yes.text == "Ja"
    labelled = answer(
        Intent.WORK_AUTHORIZATION,
        shape("Authorized to work?", T.YES_NO, options=["Yes, I am", "No, I am not"]),
        p,
    )
    assert isinstance(labelled, Suggested) and labelled.text == "Yes, I am"
    pick = answer(
        Intent.WORK_AUTHORIZATION,
        shape("Authorized?", T.SINGLE_CHOICE, options=["Yes", "No"]),
        p,
    )
    assert isinstance(pick, Suggested) and pick.options == ["Yes"]


@pytest.mark.parametrize("state", [None, WorkAuthorization.UNKNOWN])
def test_work_authorization_not_stated_needs_input(state: WorkAuthorization | None) -> None:
    result = answer(
        Intent.WORK_AUTHORIZATION, shape("Authorized?", T.YES_NO), prep(work_authorization=state)
    )
    assert result == NeedsInput("WORK_AUTHORIZATION_MISSING")


def test_needing_sponsorship_does_not_answer_the_authorization_question() -> None:
    p = prep(work_authorization=WorkAuthorization.REQUIRES_SPONSORSHIP)
    assert answer(Intent.WORK_AUTHORIZATION, shape("Authorized?", T.YES_NO), p) == NeedsInput(
        "WORK_AUTHORIZATION_AMBIGUOUS"
    )
    sponsorship = answer(Intent.SPONSORSHIP, shape("Sponsorship needed?", T.YES_NO), p)
    assert isinstance(sponsorship, Suggested) and sponsorship.text == "Ja"
    authorized = prep(work_authorization=WorkAuthorization.AUTHORIZED)
    no = answer(Intent.SPONSORSHIP, shape("Sponsorship needed?", T.YES_NO), authorized)
    assert isinstance(no, Suggested) and no.text == "Nein"


def test_application_language_is_the_applications_not_the_questions() -> None:
    result = answer(Intent.APPLICATION_LANGUAGE, shape("Preferred language?"), prep(language="en"))
    assert isinstance(result, Suggested) and result.text == "English"
    # a German question does not make the answer German
    german = answer(Intent.APPLICATION_LANGUAGE, shape("Bevorzugte Sprache"), prep(language="en"))
    assert isinstance(german, Suggested) and german.text == "English"
    assert answer(
        Intent.APPLICATION_LANGUAGE, shape("Language?"), prep(language=None)
    ) == NeedsInput("LANGUAGE_MISSING")


def test_contact_details_come_from_the_confirmed_resume() -> None:
    p = prep()
    email = answer(Intent.EMAIL, shape("Email"), p, **confirmed())
    assert isinstance(email, Suggested) and email.text == "alex.example@example.com"
    assert email.fact_ids == ["contact.email"]
    phone = answer(Intent.PHONE, shape("Phone"), p, **confirmed())
    assert isinstance(phone, Suggested) and phone.text == "+49 170 1234567"
    linkedin = answer(Intent.LINKEDIN, shape("LinkedIn"), p, **confirmed())
    assert isinstance(linkedin, Suggested) and "linkedin.com/in/alex-example-test" in str(
        linkedin.text
    )


def test_contact_details_not_in_the_resume_need_input() -> None:
    assert answer(Intent.EMAIL, shape("Email"), prep()) == NeedsInput("CONTACT_MISSING")
    assert answer(Intent.GITHUB, shape("GitHub"), prep(), **confirmed()) == NeedsInput(
        "CONTACT_MISSING"
    )
    # a yes/no question about an email address is not answered with the address
    assert answer(Intent.EMAIL, shape("Email", T.YES_NO), prep(), **confirmed()) == NeedsInput(
        "TYPE_MISMATCH"
    )


def test_rejected_resume_items_are_not_contact_details() -> None:
    data = accepted_profile_data()
    for section, item in data.iter_items():
        if section == "personal_information" and item.field == "email":
            item.review_status = "rejected"
    result = answer(Intent.EMAIL, shape("Email"), prep(), profile_id=PROFILE_ID, profile_data=data)
    assert result == NeedsInput("CONTACT_MISSING")


def test_total_experience_only_from_dates_that_support_it() -> None:
    result = answer(
        Intent.TOTAL_EXPERIENCE, shape("Years of experience", T.NUMERIC), prep(), **confirmed()
    )
    assert isinstance(result, Suggested) and result.text is not None
    assert result.text.isdigit() and int(result.text) >= 6
    assert result.fact_ids == ["derived.years"]
    assert answer(
        Intent.TOTAL_EXPERIENCE, shape("Years of experience", T.NUMERIC), prep()
    ) == NeedsInput("EXPERIENCE_NOT_DERIVABLE")


# --- AI answers: structure of the contract ----------------------------------------------------


def test_the_ai_contract_rejects_contradictions() -> None:
    for bad in (
        {"answer": "x", "needs_user_input": True, "answer_type": "NEEDS_INPUT"},
        {"answer": None, "answer_type": "TEXT"},
        {"answer": "x", "selected_options": ["A"], "answer_type": "TEXT"},
        {"answer": None, "needs_user_input": False, "answer_type": "NEEDS_INPUT"},
        {"answer": "x", "unexpected": 1},
    ):
        with pytest.raises(ValidationError):
            AIQuestionAnswer.model_validate(bad)
    assert AIQuestionAnswer.model_validate(needs_input()).needs_user_input
    assert AIQuestionAnswer.model_validate(option_answer("A")).selected_options == ["A"]


# --- AI answers: truth validation -------------------------------------------------------------


def context(
    text: str,
    question_type: T = T.FREE_TEXT,
    *,
    max_length: int | None = None,
    options: list[str] | None = None,
    language: str = "en",
    **prep_values: Any,
) -> Any:
    return build_question_context(
        question=QuestionShape(text, question_type, max_length, options),
        required=False,
        language=language,
        resume_profile_id=PROFILE_ID,
        profile_data=accepted_profile_data(),
        account_name=("Jane", "Doe"),
        job=context_job(),
        preparation=Preparation(language=language, **prep_values),
    )


def check(ctx: Any, output: dict[str, Any]) -> Any:
    return validate_answer(AIQuestionAnswer.model_validate(output), ctx)


def codes(result: Any) -> set[str]:
    return {violation.code for violation in result.report.violations}


def test_a_grounded_answer_passes() -> None:
    ctx = context("Describe your SAP experience.")
    fact = fact_with(ctx.payload and _ctx_dict(ctx), "Developed and maintained SAP IS-U")
    result = check(
        ctx, text_answer("I developed and maintained SAP IS-U applications.", fact["id"])
    )
    assert result.passed and result.text is not None


def _ctx_dict(ctx: Any) -> dict[str, Any]:
    return ctx.payload


def test_the_candidate_never_sees_salary_contact_details_or_notes() -> None:
    ctx = context("Why do you want this role?", salary_expectation=Decimal("78000"))
    shown = str(ctx.payload)
    assert "78000" not in shown and "alex.example@example.com" not in shown
    assert "+49 170" not in shown


def test_unsupported_numbers_skills_and_roles_are_rejected() -> None:
    ctx = context("Tell us about your background.")
    fact = fact_with(_ctx_dict(ctx), "Developed and maintained SAP IS-U")["id"]
    for text, expected in (
        ("I have 12 years of experience.", "UNSUPPORTED_NUMBER"),
        ("I worked with Kubernetes and Docker.", "UNSUPPORTED_SKILL"),
        ("I led a team of engineers.", "UNSUPPORTED_ROLE_CLAIM"),
        ("I won an award for this work.", "UNSUPPORTED_ACHIEVEMENT"),
        ("I worked at Globex Inc. before.", "UNSUPPORTED_ORGANIZATION"),
    ):
        result = check(ctx, text_answer(text, fact))
        assert not result.passed and expected in codes(result), (text, codes(result))


def test_an_answer_needs_a_source_and_it_must_exist() -> None:
    ctx = context("Tell us about your background.")
    assert "MISSING_SOURCE" in codes(check(ctx, text_answer("I develop SAP applications.")))
    assert "UNKNOWN_FACT_ID" in codes(check(ctx, text_answer("I develop SAP IS-U.", "exp99.r1")))
    assert "JOB_FACT_AS_SOURCE" in codes(check(ctx, text_answer("I plan projects.", "job.resp1")))


def test_company_claims_and_pay_talk_are_rejected() -> None:
    ctx = context("Why do you want to work for our company?")
    fact = fact_with(_ctx_dict(ctx), "SAP IS-U")["id"]
    claim = check(
        ctx, text_answer("I have always admired your company's innovative culture.", fact)
    )
    assert "COMPANY_KNOWLEDGE_CLAIM" in codes(claim)
    pay = check(ctx, text_answer("I expect a competitive salary of 80,000.", fact))
    assert "SALARY_MENTION_NOT_ALLOWED" in codes(pay)


def test_naming_the_position_and_company_is_not_a_claim() -> None:
    ctx = context("Why do you want to work for our company?")
    fact = fact_with(_ctx_dict(ctx), "SAP IS-U")["id"]
    result = check(
        ctx,
        text_answer(
            "I am applying for the position of Senior Project Manager at Example GmbH "
            "because I developed SAP IS-U applications.",
            fact,
        ),
    )
    assert result.passed, codes(result)


def test_answer_longer_than_the_limit_is_rejected_not_truncated() -> None:
    ctx = context("Why do you want this role?", max_length=40)
    fact = fact_with(_ctx_dict(ctx), "SAP IS-U")["id"]
    long = "I developed and maintained SAP IS-U applications for meter-to-cash processes."
    result = check(ctx, text_answer(long, fact))
    assert "ANSWER_TOO_LONG" in codes(result) and result.text is None
    short = check(ctx, text_answer("I developed SAP IS-U applications.", fact))
    assert short.passed


def test_wrong_language_is_rejected() -> None:
    ctx = context("Beschreiben Sie Ihre Erfahrung.", language="de")
    fact = fact_with(_ctx_dict(ctx), "Developed and maintained SAP IS-U")["id"]
    english = (
        "I developed and maintained the applications that are used by the team and the company "
        "for the processes of the meter and the cash of the customers with the SAP IS-U system."
    )
    assert "WRONG_LANGUAGE" in codes(check(ctx, text_answer(english, fact)))


def test_needs_user_input_is_not_a_violation() -> None:
    result = check(
        context("How many years of Java experience do you have?", T.NUMERIC), needs_input()
    )
    assert result.needs_input and result.passed and result.text is None


# Numbers: the exact case of the spec ----------------------------------------------------------


def test_a_number_the_resume_does_not_state_is_rejected() -> None:
    ctx = context("How many years of Java experience do you have?", T.NUMERIC)
    fact = fact_with(_ctx_dict(ctx), "SAP IS-U")["id"]
    result = check(ctx, text_answer("5", fact, answer_type="NUMBER"))
    assert not result.passed
    assert {"UNSUPPORTED_NUMBER", "UNSUPPORTED_TOPIC"} <= codes(result)


def test_a_stated_number_about_the_topic_is_accepted() -> None:
    ctx = context("How many percent was the batch runtime reduced?", T.NUMERIC)
    fact = fact_with(_ctx_dict(ctx), "30%")["id"]
    result = check(ctx, text_answer("30", fact, answer_type="NUMBER"))
    assert result.passed and result.text == "30"


def test_numeric_answers_must_be_plain_numbers() -> None:
    ctx = context("How many percent was the batch runtime reduced?", T.NUMERIC)
    fact = fact_with(_ctx_dict(ctx), "30%")["id"]
    assert "INVALID_ANSWER_FORMAT" in codes(check(ctx, text_answer("about thirty", fact)))


# Dates ----------------------------------------------------------------------------------------


def test_dates_must_be_valid_and_stated() -> None:
    ctx = context("Date you started at Example Energy", T.DATE, available_from=date(2027, 1, 1))
    fact = fact_with(_ctx_dict(ctx), "Available from")["id"]
    assert check(ctx, text_answer("2027-01-01", fact, answer_type="DATE")).passed
    assert "UNSUPPORTED_NUMBER" in codes(
        check(ctx, text_answer("2027-02-01", fact, answer_type="DATE"))
    )
    assert "INVALID_ANSWER_FORMAT" in codes(
        check(ctx, text_answer("2027-13-45", fact, answer_type="DATE"))
    )


# Yes / No -------------------------------------------------------------------------------------


def test_yes_needs_a_supporting_fact_about_the_topic() -> None:
    ctx = context("Do you have a driving license?", T.YES_NO)
    license_fact = fact_with(_ctx_dict(ctx), "Driving license")["id"]
    assert check(ctx, text_answer("Yes", license_fact, answer_type="YES_NO")).passed
    other = fact_with(_ctx_dict(ctx), "SAP IS-U")["id"]
    assert "UNSUPPORTED_TOPIC" in codes(check(ctx, text_answer("Yes", other, answer_type="YES_NO")))


def test_no_is_never_derived_from_a_missing_fact() -> None:
    ctx = context("Do you have a driving license?", T.YES_NO)
    other = fact_with(_ctx_dict(ctx), "SAP IS-U")["id"]
    result = check(ctx, text_answer("No", other, answer_type="YES_NO"))
    assert "UNSUPPORTED_NEGATIVE" in codes(result)


def test_yes_no_answers_must_be_yes_or_no() -> None:
    ctx = context("Do you have a driving license?", T.YES_NO)
    fact = fact_with(_ctx_dict(ctx), "Driving license")["id"]
    assert "INVALID_ANSWER_FORMAT" in codes(
        check(ctx, text_answer("Maybe", fact, answer_type="YES_NO"))
    )


# Choices --------------------------------------------------------------------------------------


def test_only_listed_options_can_be_selected() -> None:
    ctx = context(
        "Which technology do you know best?", T.SINGLE_CHOICE, options=["ABAP", "SQL", "Python"]
    )
    skill = fact_with(_ctx_dict(ctx), "ABAP")["id"]
    good = check(ctx, option_answer("abap", facts=(skill,)))
    assert good.passed and good.options == ["ABAP"]  # stored as listed
    invented = check(ctx, option_answer("ABAP / Lead", facts=(skill,)))
    assert "INVALID_OPTION" in codes(invented)


def test_a_choice_must_be_supported_by_the_cited_facts() -> None:
    ctx = context(
        "Which technology do you know best?", T.SINGLE_CHOICE, options=["ABAP", "SQL", "Python"]
    )
    skill = fact_with(_ctx_dict(ctx), "ABAP")["id"]
    assert not check(ctx, option_answer("Python", facts=(skill,))).passed


def test_single_choice_takes_one_and_multi_choice_takes_several() -> None:
    options = ["ABAP", "SQL", "Python"]
    single = context("Which?", T.SINGLE_CHOICE, options=options)
    abap = fact_with(_ctx_dict(single), "ABAP")["id"]
    sql = fact_with(_ctx_dict(single), "SQL")["id"]
    assert "INVALID_OPTION" in codes(check(single, option_answer("ABAP", "SQL", facts=(abap, sql))))
    multi = context("Which do you know?", T.MULTI_CHOICE, options=options)
    ok = check(multi, option_answer("ABAP", "SQL", facts=(abap, sql)))
    assert ok.passed and ok.options == ["ABAP", "SQL"]
    assert not check(multi, option_answer("ABAP", "Python", facts=(abap, sql))).passed
    assert "INVALID_OPTION" in codes(check(multi, option_answer("ABAP", "ABAP", facts=(abap,))))


def test_free_text_for_a_choice_and_options_for_free_text_are_rejected() -> None:
    choice = context("Which?", T.SINGLE_CHOICE, options=["ABAP", "SQL"])
    abap = fact_with(_ctx_dict(choice), "ABAP")["id"]
    assert "INVALID_OPTION" in codes(check(choice, text_answer("ABAP", abap)))
    free = context("Describe your SAP experience.")
    sap = fact_with(_ctx_dict(free), "SAP IS-U")["id"]
    mixed = {**option_answer("ABAP", facts=(sap,))}
    assert "INVALID_OPTION" in codes(check(free, mixed))


# --- Prompt pinning ---------------------------------------------------------------------------


def test_prompt_fingerprint_is_pinned() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "ai" / "prompts"
    for version, fingerprint in prompts.PROMPT_FINGERPRINTS.items():
        assert hashlib.sha256((path / f"{version}.txt").read_bytes()).hexdigest() == fingerprint
    assert (
        prompts.current_prompt_hash() == prompts.PROMPT_FINGERPRINTS[prompts.ANSWER_PROMPT_VERSION]
    )


def test_prompt_states_the_truth_rules() -> None:
    prompt = prompts.system_prompt()
    for rule in ("needs_user_input", 'do NOT answer "No"', "max_length", "not listed"):
        assert rule in prompt


def test_user_prompt_carries_the_question_as_data() -> None:
    ctx = context("Why do you want this role?", max_length=300)
    text = prompts.user_prompt(ctx)
    assert "<question_context>" in text and '"max_length": 300' in text


def test_needs_input_messages_cover_every_reason_the_resolver_uses() -> None:
    for reason in (
        "SALARY_MISSING",
        "START_DATE_MISSING",
        "DATE_NOT_STATED",
        "NOTICE_PERIOD_MISSING",
        "WORK_AUTHORIZATION_MISSING",
        "WORK_AUTHORIZATION_AMBIGUOUS",
        "LANGUAGE_MISSING",
        "CONTACT_MISSING",
        "EXPERIENCE_NOT_DERIVABLE",
        "TYPE_MISMATCH",
        "OPTION_NOT_MATCHED",
        "UNIT_MISMATCH",
        "ANSWER_TOO_LONG",
        "FACTS_INSUFFICIENT",
    ):
        assert deterministic.needs_input_message(reason)
    assert deterministic.needs_input_message(None) is None
