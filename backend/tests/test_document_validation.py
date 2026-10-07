"""V0.8 truth validation: invented claims are rejected, supported ones survive.

These run the validator on structured AI output directly (no HTTP, no
database), against the fixture resume's facts. The resume:

    Example Energy GmbH  SAP IS-U Developer  01/2023-present   (exp1)
    Sample Consulting AG SAP ABAP Developer  03/2019-12/2022   (exp2)
    skills SAP ABAP (expert), SAP IS-U, SAP BTP, SQL, Git; languages German,
    English C1, Spanish; certification SAP Certified Development Associate;
    M.Sc. Wirtschaftsinformatik; "Reduced batch runtime by 30%".
"""

import copy
from typing import Any

import pytest

from app.schemas.document_ai import AICoverLetter, AITailoredCV
from app.services.documents import validation
from tests.document_helpers import build_context, faithful_cv, faithful_letter


def cv_output(context, **changes: Any) -> AITailoredCV:  # type: ignore[no-untyped-def]
    output = faithful_cv(context.payload)
    output.update(changes)
    return AITailoredCV.model_validate(output)


def codes(report) -> set[str]:  # type: ignore[no-untyped-def]
    return {v.code for v in report.violations}


def with_bullet(context, text: str, ids: list[str], *, entry: int = 0) -> AITailoredCV:  # type: ignore[no-untyped-def]
    output = faithful_cv(context.payload)
    output["experience"][entry]["bullets"][0] = {"text": text, "source_fact_ids": ids}
    return AITailoredCV.model_validate(output)


def with_summary(context, text: str, ids: list[str]) -> AITailoredCV:  # type: ignore[no-untyped-def]
    return cv_output(context, summary={"text": text, "source_fact_ids": ids})


#: A tool or technology that isn't in the cited facts is reported either as a
#: taxonomy skill or, when the taxonomy doesn't know it, as a technology token.
TOOL_CODES = {"UNSUPPORTED_SKILL", "UNSUPPORTED_TECHNOLOGY"}


@pytest.fixture(params=["de", "en"])
def language(request) -> str:  # type: ignore[no-untyped-def]
    return request.param  # type: ignore[no-any-return]


# --- What survives ----------------------------------------------------------------------------


def test_a_faithful_cv_passes(language: str) -> None:
    context = build_context("cv", language=language)
    report = validation.validate_cv(cv_output(context), context)
    assert report.status == "passed", report.violations
    assert report.text_units_checked == 6  # summary + five bullets


def test_a_paraphrase_is_not_an_exact_match_and_still_passes() -> None:
    """Validation checks factual support, not string equality."""
    context = build_context("cv", language="en")
    output = with_bullet(
        context,
        "Took care of building and maintaining SAP IS-U software for meter-to-cash processing.",
        ["exp1.r1"],
    )
    report = validation.validate_cv(output, context)
    assert report.status == "passed", report.violations
    assert "Took care of" not in context.catalog.get("exp1.r1").text


def test_supported_numbers_terms_and_credentials_survive() -> None:
    context = build_context("cv", language="en")
    cases = [
        ("Cut batch runtime by 30% by optimizing code.", ["exp1.a1"]),
        ("Developed ABAP OO enhancements on S/4HANA.", ["exp1.r2", "exp1.t"]),
        ("Worked at Example Energy GmbH on SAP IS-U.", ["exp1.r1"]),  # employer in its header
    ]
    for text, ids in cases:
        report = validation.validate_cv(with_bullet(context, text, ids), context)
        assert report.status == "passed", (text, report.violations)
    summary = with_summary(
        context,
        "SAP developer with 7 years of professional experience, certified as SAP Certified "
        "Development Associate, with SQL and German and English skills.",
        ["summary", "derived.years", "cert1", "skill4", "lang1", "lang2"],
    )
    report = validation.validate_cv(summary, context)
    assert report.status == "passed", report.violations


def test_supported_leadership_is_allowed_when_a_fact_says_so() -> None:
    data = build_context().candidate  # noqa: F841 - the default resume says nothing about leading
    from tests.document_helpers import accepted_profile_data

    resume = accepted_profile_data()
    resume.work_experience[0].responsibilities[0] = "Led a team of 4 developers"
    context = build_context("cv", language="en", data=resume)
    report = validation.validate_cv(
        with_bullet(context, "Led four developers.", ["exp1.r1"]), context
    )
    assert "UNSUPPORTED_ROLE_CLAIM" not in codes(report)
    assert "UNSUPPORTED_NUMBER" not in codes(report)  # "four" = the cited 4


# --- Invented claims --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # technology: not in the resume, even though the job asks for it
        ("Deployed services to AWS.", "UNSUPPORTED_TECHNOLOGY"),
        ("Automated infrastructure with Terraform and Kubernetes.", "UNSUPPORTED_SKILL"),
        ("Built dashboards in PostgreSQL.", "UNSUPPORTED_TECHNOLOGY"),
        ("Wrote services in C++.", "UNSUPPORTED_TECHNOLOGY"),
        ("Used Python for data work.", "UNSUPPORTED_SKILL"),
        # employer
        ("Developed ABAP programs at Initech GmbH.", "UNSUPPORTED_ORGANIZATION"),
        ("Developed ABAP programs for Google.", "UNSUPPORTED_NAME"),
        # certification / degree
        ("Developed ABAP programs; AWS Certified Solutions Architect.", "UNSUPPORTED_CREDENTIAL"),
        ("Developed ABAP programs after completing an MBA.", "UNSUPPORTED_CREDENTIAL"),
        ("Developed ABAP programs as a PhD researcher.", "UNSUPPORTED_CREDENTIAL"),
        # language
        ("Developed ABAP programs and spoke fluent French with clients.", "UNSUPPORTED_LANGUAGE"),
        # years / numeric results
        ("Developed ABAP programs for 10 years.", "UNSUPPORTED_NUMBER"),
        ("Cut batch runtime by 45%.", "UNSUPPORTED_NUMBER"),
        ("Developed ABAP programs for twelve clients.", "UNSUPPORTED_NUMBER"),
        ("Saved the company 2 million euros.", "UNSUPPORTED_NUMBER"),
        ("Developed ABAP programs across decades of projects.", "UNSUPPORTED_EXPERIENCE_CLAIM"),
        # achievements
        ("Won an award for ABAP programs.", "UNSUPPORTED_ACHIEVEMENT"),
        ("Doubled the throughput of ABAP programs.", "UNSUPPORTED_ACHIEVEMENT"),
        ("Published a paper about ABAP programs.", "UNSUPPORTED_ACHIEVEMENT"),
        # responsibility / seniority
        ("Led a team of ABAP developers.", "UNSUPPORTED_ROLE_CLAIM"),
        ("Managed the ABAP development department.", "UNSUPPORTED_ROLE_CLAIM"),
        ("Acted as Head of ABAP development.", "UNSUPPORTED_ROLE_CLAIM"),
        ("Mentored junior ABAP developers.", "UNSUPPORTED_ROLE_CLAIM"),
        ("Founded the ABAP competence center.", "UNSUPPORTED_ROLE_CLAIM"),
        # pay
        ("Developed ABAP programs for a salary of 80000 EUR.", "SALARY_MENTION_NOT_ALLOWED"),
        (
            "Developed ABAP programs in a well-paid role (compensation above market).",
            "SALARY_MENTION_NOT_ALLOWED",
        ),
    ],
)
def test_invented_claims_are_rejected(text: str, expected: str) -> None:
    context = build_context("cv", language="en")
    report = validation.validate_cv(with_bullet(context, text, ["exp1.r1"]), context)
    assert report.status == "failed"
    wanted = TOOL_CODES if expected == "UNSUPPORTED_TECHNOLOGY" else {expected}
    assert wanted & codes(report), (text, [(v.code, v.excerpt) for v in report.violations])
    assert all(v.location == "experience[0].bullets[0]" for v in report.violations)


@pytest.mark.parametrize(
    "text",
    [
        "Entwickelte Services in AWS.",
        "Leitete ein Team von ABAP-Entwicklern.",
        "Geleitet wurden drei Projekte.",
        "Zertifizierter Spezialist für ABAP-Programme.",
        "Entwickelte ABAP-Programme für Kunden in Köln bei Initech GmbH.",
    ],
)
def test_german_text_is_checked_too(text: str) -> None:
    context = build_context("cv", language="de")
    report = validation.validate_cv(with_bullet(context, text, ["exp1.r1"]), context)
    assert report.status == "failed", text


def test_job_requirements_do_not_support_claims_about_the_candidate() -> None:
    """The job asks for AWS and Terraform; the candidate never listed them."""
    context = build_context("cv", language="en")
    assert "AWS and Terraform" in str(context.payload["target_job"])
    output = with_summary(context, "SAP developer with AWS and Terraform skills.", ["summary"])
    assert TOOL_CODES & codes(validation.validate_cv(output, context))


def test_the_job_title_is_not_a_claim_about_the_candidate() -> None:
    context = build_context("cv", language="en")
    output = with_summary(context, "A Senior Project Manager with SAP experience.", ["summary"])
    assert "UNSUPPORTED_ROLE_CLAIM" in codes(validation.validate_cv(output, context))


def test_a_claim_is_checked_against_the_facts_it_cites_not_the_whole_resume() -> None:
    """SQL is a skill of the candidate, but the bullet cites a fact that does not say it."""
    context = build_context("cv", language="en")
    report = validation.validate_cv(
        with_bullet(context, "Wrote SQL queries for meter data.", ["exp1.r1"]), context
    )
    assert "UNSUPPORTED_SKILL" in codes(report)
    cited = with_bullet(context, "Wrote SQL queries for meter data.", ["exp1.r1", "skill4"])
    # skill4 is not a fact of this role, so it cannot be cited by the bullet either
    assert "FOREIGN_FACT" in codes(validation.validate_cv(cited, context))


# --- Structure and traceability ---------------------------------------------------------------


def test_every_text_must_cite_facts_that_exist() -> None:
    context = build_context("cv", language="en")
    report = validation.validate_cv(with_bullet(context, "Developed ABAP programs.", []), context)
    assert "MISSING_SOURCE" in codes(report)
    report = validation.validate_cv(
        with_bullet(context, "Developed ABAP programs.", ["exp1.r99"]), context
    )
    assert "UNKNOWN_FACT_ID" in codes(report)
    report = validation.validate_cv(
        with_bullet(context, "Developed ABAP programs.", ["job.resp1"]), context
    )
    assert "JOB_FACT_AS_SOURCE" in codes(report)


def test_a_bullet_may_only_cite_facts_of_its_own_role() -> None:
    context = build_context("cv", language="en")
    report = validation.validate_cv(
        with_bullet(context, "Developed ABAP programs.", ["exp2.r1"], entry=0), context
    )
    assert "FOREIGN_FACT" in codes(report)


def test_every_role_appears_exactly_once_and_only_real_roles() -> None:
    context = build_context("cv", language="en")
    base = faithful_cv(context.payload)

    missing = copy.deepcopy(base)
    missing["experience"].pop()
    assert "MISSING_EXPERIENCE" in codes(
        validation.validate_cv(AITailoredCV.model_validate(missing), context)
    )
    duplicate = copy.deepcopy(base)
    duplicate["experience"].append(copy.deepcopy(duplicate["experience"][0]))
    assert "DUPLICATE_EXPERIENCE" in codes(
        validation.validate_cv(AITailoredCV.model_validate(duplicate), context)
    )
    invented = copy.deepcopy(base)
    invented["experience"].append({"source_experience_id": "exp9", "bullets": []})
    assert "UNKNOWN_EXPERIENCE" in codes(
        validation.validate_cv(AITailoredCV.model_validate(invented), context)
    )


def test_reordering_roles_is_allowed() -> None:
    context = build_context("cv", language="en")
    base = faithful_cv(context.payload)
    base["experience"].reverse()
    assert validation.validate_cv(AITailoredCV.model_validate(base), context).status == "passed"


def test_skills_must_be_existing_skill_facts_without_duplicates() -> None:
    context = build_context("cv", language="en")
    base = faithful_cv(context.payload)
    base["skills"].append({"source_fact_id": "skill9", "emphasized": True})  # "AWS", not listed
    base["skills"].append({"source_fact_id": "skill1", "emphasized": False})
    found = codes(validation.validate_cv(AITailoredCV.model_validate(base), context))
    assert {"UNKNOWN_SKILL", "DUPLICATE_SKILL"} <= found
    # leaving skills out (prioritizing) is fine
    base = faithful_cv(context.payload)
    base["skills"] = base["skills"][:2]
    assert validation.validate_cv(AITailoredCV.model_validate(base), context).status == "passed"


def test_the_cv_must_be_in_the_application_language() -> None:
    context = build_context("cv", language="de")
    english = faithful_cv(build_context("cv", language="en").payload)
    report = validation.validate_cv(AITailoredCV.model_validate(english), context)
    assert "WRONG_LANGUAGE" in codes(report)


def test_empty_text_is_rejected() -> None:
    context = build_context("cv", language="en")
    report = validation.validate_cv(with_bullet(context, "  ", ["exp1.r1"]), context)
    assert "EMPTY_TEXT" in codes(report)


def test_the_report_lists_what_was_rejected_and_where() -> None:
    context = build_context("cv", language="en")
    report = validation.validate_cv(
        with_bullet(context, "Deployed services to AWS.", ["exp1.r1"]), context
    )
    violation = next(v for v in report.violations if v.excerpt == "AWS")
    assert violation.location == "experience[0].bullets[0]"
    assert violation.message


# --- Cover letter -----------------------------------------------------------------------------


def letter(context, **changes: Any) -> AICoverLetter:  # type: ignore[no-untyped-def]
    output = faithful_letter(context.payload)
    output.update(changes)
    return AICoverLetter.model_validate(output)


def with_paragraph(context, index: int, **changes: Any) -> AICoverLetter:  # type: ignore[no-untyped-def]
    output = faithful_letter(context.payload)
    output["paragraphs"][index].update(changes)
    return AICoverLetter.model_validate(output)


def test_a_faithful_letter_passes(language: str) -> None:
    context = build_context("cover_letter", language=language)
    report = validation.validate_letter(letter(context), context)
    assert report.status == "passed", report.violations


def test_the_letter_may_name_the_job_title_and_company() -> None:
    context = build_context("cover_letter", language="en")
    text = (
        "I am applying for the position of Senior Project Manager at Example GmbH in Köln, "
        "and I am available from 1 January 2027 with a notice period of 3 months."
    )
    context = build_context(
        "cover_letter",
        language="en",
        prep={
            "available_from": __import__("datetime").date(2027, 1, 1),
            "notice_period": (
                3,
                __import__(
                    "app.models.application", fromlist=["NoticePeriodUnit"]
                ).NoticePeriodUnit.MONTHS,
            ),
        },
    )
    report = validation.validate_letter(
        with_paragraph(
            context, 0, text=text, source_fact_ids=["app.available_from", "app.notice_period"]
        ),
        context,
    )
    assert report.status == "passed", report.violations


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I am a Senior Project Manager with deep experience.", "UNSUPPORTED_ROLE_CLAIM"),
        (
            "I admire your company culture and have long followed your work.",
            "COMPANY_KNOWLEDGE_CLAIM",
        ),
        ("Example GmbH is a market leader in its field.", "COMPANY_KNOWLEDGE_CLAIM"),
        ("I would gladly relocate for this position.", "PERSONAL_CIRCUMSTANCE_CLAIM"),
        ("As a father of two children I value stability.", "PERSONAL_CIRCUMSTANCE_CLAIM"),
        ("I have used AWS in many projects.", "UNSUPPORTED_TECHNOLOGY"),
        ("My salary expectation is 90000 EUR.", "SALARY_MENTION_NOT_ALLOWED"),
        ("I bring 15 years of experience.", "UNSUPPORTED_NUMBER"),
    ],
)
def test_invented_letter_claims_are_rejected(text: str, expected: str) -> None:
    context = build_context("cover_letter", language="en")
    report = validation.validate_letter(
        with_paragraph(context, 1, text=text, source_fact_ids=["exp1", "exp1.r1"]), context
    )
    assert report.status == "failed"
    wanted = TOOL_CODES if expected == "UNSUPPORTED_TECHNOLOGY" else {expected}
    assert wanted & codes(report), (text, [(v.code, v.excerpt) for v in report.violations])


def test_a_letter_in_the_wrong_language_is_rejected() -> None:
    context = build_context("cover_letter", language="de")
    english = AICoverLetter.model_validate(
        faithful_letter(build_context("cover_letter", language="en").payload)
    )
    assert "WRONG_LANGUAGE" in codes(validation.validate_letter(english, context))
    mislabelled = AICoverLetter.model_validate(
        {**faithful_letter(context.payload), "language": "en"}
    )
    assert "WRONG_LANGUAGE" in codes(validation.validate_letter(mislabelled, context))


def test_the_letter_needs_its_structure_and_citations() -> None:
    context = build_context("cover_letter", language="en")
    base = faithful_letter(context.payload)

    no_opening = copy.deepcopy(base)
    no_opening["paragraphs"].pop(0)
    assert "INVALID_STRUCTURE" in codes(
        validation.validate_letter(AICoverLetter.model_validate(no_opening), context)
    )
    no_closing = copy.deepcopy(base)
    no_closing["paragraphs"].pop()
    assert "INVALID_STRUCTURE" in codes(
        validation.validate_letter(AICoverLetter.model_validate(no_closing), context)
    )
    uncited = copy.deepcopy(base)
    uncited["paragraphs"][1]["source_fact_ids"] = []
    assert "MISSING_SOURCE" in codes(
        validation.validate_letter(AICoverLetter.model_validate(uncited), context)
    )
    application_only = copy.deepcopy(base)
    application_only["paragraphs"][1]["source_fact_ids"] = ["app.notice_period"]
    assert "MISSING_SOURCE" in codes(
        validation.validate_letter(AICoverLetter.model_validate(application_only), context)
    )
    unknown_job_fact = copy.deepcopy(base)
    unknown_job_fact["paragraphs"][0]["job_fact_ids"] = ["job.resp99"]
    assert "UNKNOWN_FACT_ID" in codes(
        validation.validate_letter(AICoverLetter.model_validate(unknown_job_fact), context)
    )


def test_a_cv_never_refers_to_the_target_job_or_company() -> None:
    """Unlike a letter, a CV has no reason to name the job: nothing is exempt."""
    context = build_context("cv", language="en")
    for text in (
        "Looking forward to working at Example GmbH.",
        "Applying for the position of Senior Project Manager.",
    ):
        report = validation.validate_cv(with_summary(context, text, ["summary"]), context)
        assert report.status == "failed", text


def test_claiming_to_hold_the_target_job_title_is_a_claim_not_a_reference() -> None:
    context = build_context("cover_letter", language="en")
    for text in (
        "As a Senior Project Manager I have led many projects.",
        "I worked as a Senior Project Manager for years.",
    ):
        report = validation.validate_letter(
            with_paragraph(context, 1, text=text, source_fact_ids=["exp1", "exp1.r1"]), context
        )
        assert "UNSUPPORTED_ROLE_CLAIM" in codes(report), text


def test_claiming_past_employment_at_the_target_company_is_rejected() -> None:
    context = build_context("cover_letter", language="en")
    report = validation.validate_letter(
        with_paragraph(
            context,
            1,
            text="I previously worked at Example GmbH on ABAP programs.",
            source_fact_ids=["exp1", "exp1.r1"],
        ),
        context,
    )
    assert "UNSUPPORTED_ORGANIZATION" in codes(report)
    # ... unless the candidate's own resume says so
    from tests.document_helpers import accepted_profile_data

    resume = accepted_profile_data()
    resume.work_experience[1].company = "Example GmbH"
    context = build_context("cover_letter", language="en", data=resume)
    report = validation.validate_letter(
        with_paragraph(
            context,
            1,
            text="I previously worked at Example GmbH on ABAP programs.",
            source_fact_ids=["exp2", "exp2.r1"],
        ),
        context,
    )
    assert "UNSUPPORTED_ORGANIZATION" not in codes(report)


def test_referring_to_the_position_company_and_place_is_not_a_claim() -> None:
    context = build_context("cover_letter", language="en")
    for text in (
        "I am applying for the Senior Project Manager position at Example GmbH in Köln.",
        "I would like to apply as Senior Project Manager with Example GmbH.",
    ):
        report = validation.validate_letter(with_paragraph(context, 0, text=text), context)
        assert report.status == "passed", (text, report.violations)


# --- German claim lexicon ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Teamleiter im Bereich SAP",
        "Unterstützte die Projektleitung",
        "Abteilungsleiterin für die Entwicklung",
        "Mitarbeiterführung und Budgetverantwortung",
        "Softwarearchitekt für Schnittstellen",
        "Mitgründer des Teams",
        "Leitete ein Team von Entwicklern",
    ],
)
def test_german_management_compounds_are_claims(text: str) -> None:
    """Teamleiter, Projektleitung ... are how German says "I led": they count."""
    from app.services.documents import claims

    assert "UNSUPPORTED_ROLE_CLAIM" in claims.claim_groups(text)


@pytest.mark.parametrize(
    "text",
    [
        "Führte die Migration der Schnittstellen auf S/4HANA durch.",
        "Verantwortete die Umsetzung der Reports.",
        "Erstellte Anleitungen und einen Leitfaden.",
        "Buchführung und Begleiten von Kunden.",
        "Software architecture review.",
    ],
)
def test_ordinary_german_and_english_work_verbs_are_not_leadership_claims(text: str) -> None:
    from app.services.documents import claims

    assert "UNSUPPORTED_ROLE_CLAIM" not in claims.claim_groups(text)


def test_a_german_leadership_word_is_supported_by_a_German_fact() -> None:
    from tests.document_helpers import accepted_profile_data

    resume = accepted_profile_data()
    resume.work_experience[0].responsibilities[0] = "Teamleitung für vier Entwickler"
    context = build_context("cv", language="de", data=resume)
    ok = validation.validate_cv(
        with_bullet(context, "Leitung eines Teams von vier Entwicklern.", ["exp1.r1"]), context
    )
    assert "UNSUPPORTED_ROLE_CLAIM" not in codes(ok)
    bad = validation.validate_cv(
        with_bullet(context, "Leitung eines Teams von vier Entwicklern.", ["exp1.r2"]), context
    )
    assert "UNSUPPORTED_ROLE_CLAIM" in codes(bad)
