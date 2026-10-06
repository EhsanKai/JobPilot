"""V0.8: the fact catalogue and the deterministic generation context."""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.models.application import NoticePeriodUnit, WorkAuthorization
from app.models.generated_document import GeneratedDocumentType
from app.services.documents.context import (
    GENERATION_LANGUAGES,
    MatchGuidance,
    Preparation,
    format_amount,
    salary_statement,
)
from app.services.documents.facts import YEARS_FACT_ID
from app.services.documents.prompts import (
    PROMPT_FINGERPRINTS,
    PROMPT_VERSIONS,
    system_prompt,
    user_prompt,
)
from tests.document_helpers import accepted_profile_data, build_context, context_job


def _texts(context) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {fact.id: fact.text for fact in context.catalog}


# --- Facts ------------------------------------------------------------------------------------


def test_facts_come_from_the_confirmed_profile_with_stable_ids() -> None:
    facts = _texts(build_context())
    assert facts["summary"].startswith("SAP developer with experience")
    assert facts["exp1"] == "SAP IS-U Developer - Example Energy GmbH, Berlin (01/2023 - present)"
    assert (
        facts["exp1.r1"]
        == "Developed and maintained SAP IS-U applications for meter-to-cash processes"
    )
    assert facts["exp1.a1"] == "Reduced batch runtime by 30% through code optimization"
    assert facts["exp1.t"] == "Technologies: SAP IS-U, ABAP OO, S/4HANA"
    assert facts["exp2.r2"] == "Worked with SAP BTP for side-by-side extensions"
    assert facts["edu1"].startswith("M.Sc., Wirtschaftsinformatik, Technische Universität Example")
    assert "ABAP" in facts["skill1"] and "expert" in facts["skill1"]
    assert facts["lang2"] == "English - C1"
    assert facts["lang3"] == "Spanish"
    assert facts["cert1"].startswith("SAP Certified Development Associate")
    assert facts["proj1"].startswith("Smart Meter Dashboard")
    assert facts["add1"] == "Driving license: Class B"


def test_the_same_profile_gives_the_same_ids_texts_and_hash() -> None:
    first, second = build_context(), build_context()
    assert _texts(first) == _texts(second)
    assert first.input_hash == second.input_hash
    assert first.payload == second.payload


def test_only_accepted_or_edited_items_are_facts() -> None:
    data = accepted_profile_data()
    data.work_experience[1].review_status = "rejected"
    data.skills[0].review_status = "pending"
    data.skills[1].review_status = "edited"
    facts = _texts(build_context(data=data))
    assert "exp2" not in facts and "exp2.r1" not in facts
    assert not any("SAP ABAP" in text for key, text in facts.items() if key.startswith("skill"))
    assert any(
        text.startswith("SAP IS-U") for key, text in facts.items() if key.startswith("skill")
    )


def test_a_rejected_section_leaves_no_facts() -> None:
    facts = _texts(build_context(data=accepted_profile_data(reject=("certifications", "summary"))))
    assert not any(key.startswith("cert") for key in facts)
    assert "summary" not in facts


def test_derived_years_only_when_the_dates_fully_support_them() -> None:
    assert _texts(build_context())[YEARS_FACT_ID].startswith("7 years")
    data = accepted_profile_data()
    data.work_experience[1].start_date = None  # one role has no usable start date
    assert YEARS_FACT_ID not in _texts(build_context(data=data))


def test_header_comes_from_the_resume_and_falls_back_to_the_account_name() -> None:
    context = build_context("cover_letter")
    assert context.candidate.header.full_name == "Alex Example"
    assert context.sender.full_name == "Alex Example"
    assert context.sender.email == "alex.example@example.com"
    data = accepted_profile_data(reject=("personal_information",))
    context = build_context("cover_letter", data=data)
    assert context.sender.full_name == "Jane Doe"
    assert context.sender.email is None


# --- What the AI is shown ---------------------------------------------------------------------


def test_the_prompt_payload_carries_no_contact_details_notes_or_salary() -> None:
    context = build_context(
        "cover_letter",
        prep={
            "salary_expectation": Decimal("78000"),
            "salary_currency": "EUR",
            "mention_salary_in_letter": True,
        },
    )
    prompt = user_prompt(context)
    for private in ("alex.example@example.com", "+49 170", "linkedin", "78000", "78.000", "EUR"):
        assert private not in prompt
    assert json.loads(prompt.split("<generation_context>\n")[1].split("\n</generation_context>")[0])


def test_the_job_comes_from_the_snapshot_and_is_bounded() -> None:
    many = [f"Responsibility number {n} " + "x" * 400 for n in range(40)]
    context = build_context(job=context_job(responsibilities=many, technologies=["T"] * 80))
    job = context.payload["target_job"]
    assert job["title"] == "Senior Project Manager" and job["company"] == "Example GmbH"
    assert len(job["responsibilities"]) == 15
    assert all(len(item["text"]) <= 300 for item in job["responsibilities"])
    assert len(job["technologies"]) == 30
    assert "job.resp1" in context.job_fact_ids and "job.title" in context.job_fact_ids


def test_a_posting_without_lists_contributes_a_bounded_description() -> None:
    job = context_job(
        requirements=[], responsibilities=[], technologies=[], description="Text. " * 2000
    )
    excerpt = build_context(job=job).payload["target_job"]["description_excerpt"]
    assert 0 < len(excerpt) <= 3000


def test_application_facts_and_match_guidance_are_for_the_letter_only() -> None:
    prep = {
        "available_from": date(2027, 1, 1),
        "notice_period": (3, NoticePeriodUnit.MONTHS),
        "work_authorization": WorkAuthorization.AUTHORIZED,
    }
    match = MatchGuidance(strong_matches=("SAP ABAP (required)",), gaps=("AWS (required)",))
    letter = build_context("cover_letter", prep=prep, match=match)
    cv = build_context("cv", prep=prep, match=match)
    assert [f["id"] for f in letter.payload["application_facts"]] == [
        "app.available_from",
        "app.notice_period",
        "app.work_authorization",
    ]
    assert _texts(letter)["app.notice_period"] == "Notice period: 3 months"
    assert "match_guidance" in letter.payload
    assert "application_facts" not in cv.payload and "match_guidance" not in cv.payload
    assert not any(key.startswith("app.") for key in _texts(cv))


def test_requires_sponsorship_and_unknown_authorization() -> None:
    sponsored = build_context(
        "cover_letter", prep={"work_authorization": WorkAuthorization.REQUIRES_SPONSORSHIP}
    )
    assert "sponsorship" in _texts(sponsored)["app.work_authorization"]
    unknown = build_context("cover_letter", prep={"work_authorization": WorkAuthorization.UNKNOWN})
    assert "app.work_authorization" not in _texts(unknown)


def test_the_input_hash_changes_with_every_input_that_changes_the_document() -> None:
    base = build_context("cover_letter").input_hash
    assert build_context("cv").input_hash != base
    assert build_context("cover_letter", language="en").input_hash != base
    assert build_context("cover_letter", job=context_job(title="Other")).input_hash != base
    assert (
        build_context("cover_letter", prep={"available_from": date(2027, 1, 1)}).input_hash != base
    )
    assert (
        build_context("cover_letter", match=MatchGuidance(strong_matches=("x",))).input_hash != base
    )
    salary = {"salary_expectation": Decimal(1), "salary_currency": "EUR"}
    assert (
        build_context("cover_letter", prep={**salary, "mention_salary_in_letter": True}).input_hash
        != build_context(
            "cover_letter", prep={**salary, "mention_salary_in_letter": False}
        ).input_hash
    )
    data = accepted_profile_data()
    data.work_experience[0].responsibilities[0] = "Changed"
    assert build_context("cover_letter", data=data).input_hash != base
    data = accepted_profile_data()
    data.personal_information[2].value = "+49 1"  # contact details are part of the document
    assert build_context("cover_letter", data=data).input_hash != base


# --- Salary -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "language", "expected"),
    [
        (Decimal("78000"), "de", "78.000"),
        (Decimal("78000"), "en", "78,000"),
        (Decimal("78000.50"), "de", "78.000,50"),
        (Decimal("78000.50"), "en", "78,000.50"),
        (Decimal("950"), "de", "950"),
        (Decimal("1234567"), "en", "1,234,567"),
    ],
)
def test_amounts_are_formatted_for_the_language(amount, language, expected) -> None:  # type: ignore[no-untyped-def]
    assert format_amount(amount, language) == expected


def test_the_salary_sentence_exists_only_when_the_user_chose_to_mention_it() -> None:
    def prep(mention: bool | None, **extra) -> Preparation:  # type: ignore[no-untyped-def]
        return Preparation(
            language="de",
            salary_expectation=Decimal("78000"),
            salary_currency="EUR",
            mention_salary_in_letter=mention,
            **extra,
        )

    assert (
        salary_statement(prep(True))
        == "Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr."
    )
    assert salary_statement(prep(False)) is None
    assert salary_statement(prep(None)) is None  # never assumed
    assert salary_statement(Preparation(language="de", mention_salary_in_letter=True)) is None
    english = Preparation(
        language="en",
        salary_expectation=Decimal("78000"),
        salary_currency="EUR",
        mention_salary_in_letter=True,
    )
    assert salary_statement(english) == "My salary expectation is 78,000 EUR gross per year."


# --- Prompts ----------------------------------------------------------------------------------


def test_every_document_type_has_an_explicit_versioned_prompt() -> None:
    assert PROMPT_VERSIONS[GeneratedDocumentType.TAILORED_CV] == "tailored_cv_v1"
    assert PROMPT_VERSIONS[GeneratedDocumentType.COVER_LETTER] == "cover_letter_v1"
    for document_type in GeneratedDocumentType:
        assert system_prompt(document_type)


def test_a_released_prompt_cannot_be_edited_in_place() -> None:
    """Changing a released prompt needs a new version file and a new entry,
    never an edit: its fingerprint is pinned."""
    import hashlib
    from pathlib import Path

    prompts = Path(__file__).resolve().parents[1] / "app" / "ai" / "prompts"
    for version, fingerprint in PROMPT_FINGERPRINTS.items():
        assert hashlib.sha256((prompts / f"{version}.txt").read_bytes()).hexdigest() == fingerprint


def test_generation_languages_match_the_application_languages() -> None:
    from app.schemas.application import SUPPORTED_APPLICATION_LANGUAGES

    assert set(GENERATION_LANGUAGES) == set(SUPPORTED_APPLICATION_LANGUAGES)
