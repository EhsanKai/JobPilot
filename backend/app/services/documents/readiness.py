"""Can a document be generated now? (V0.8)

A deterministic checklist, computed from the application's saved data, that
the generation endpoints enforce and the Documents section shows. The server
is the authority: the frontend does not re-implement these rules.

Nothing is guessed to unblock a generation: no language is inferred from the
job, no salary decision is made for the user, and documents are only built
from a *confirmed* resume.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.models.generated_document import GeneratedDocumentType
from app.schemas.generated_documents import DocumentBlocker, DocumentReadiness
from app.services.documents.context import GENERATION_LANGUAGES


@dataclass(frozen=True)
class ReadinessInputs:
    has_context: bool
    has_resume: bool
    resume_version_number: int | None
    has_confirmed_profile: bool
    has_facts: bool
    language: str | None
    salary_expectation: Decimal | None
    mention_salary_in_letter: bool | None


def blockers(
    inputs: ReadinessInputs, document_type: GeneratedDocumentType
) -> list[DocumentBlocker]:
    found: list[DocumentBlocker] = []

    def block(code: str, message: str) -> None:
        found.append(DocumentBlocker(code=code, message=message))

    if not inputs.has_context:
        block(
            "APPLICATION_CONTEXT_MISSING",
            "This application has no saved job context, so documents can't be generated for it.",
        )
    if not inputs.has_resume:
        block(
            "RESUME_MISSING",
            "This application has no resume. Select or upload a resume first.",
        )
    elif not inputs.has_confirmed_profile:
        block(
            "RESUME_NOT_CONFIRMED",
            f"Review and confirm the analysis of resume version {inputs.resume_version_number} "
            "first: documents are only built from resume facts you have confirmed.",
        )
    elif not inputs.has_facts:
        block(
            "RESUME_FACTS_MISSING",
            "Your confirmed resume has no accepted facts to build a document from.",
        )

    if inputs.language is None:
        block(
            "LANGUAGE_MISSING",
            "Select the application language first. JobPilot never guesses it from the job.",
        )
    elif inputs.language not in GENERATION_LANGUAGES:
        block(
            "LANGUAGE_NOT_SUPPORTED",
            "Documents can only be generated in German or English.",
        )

    if document_type is GeneratedDocumentType.COVER_LETTER:
        if inputs.mention_salary_in_letter is None and inputs.salary_expectation is not None:
            block(
                "SALARY_DECISION_REQUIRED",
                "You set a salary expectation but haven't decided whether the letter should "
                "mention it. Choose Yes or No under Application preparation.",
            )
        if inputs.mention_salary_in_letter is True and inputs.salary_expectation is None:
            block(
                "SALARY_EXPECTATION_REQUIRED",
                "You chose to mention your salary expectation in the letter, but none is set. "
                "Enter it or choose not to mention it.",
            )
    return found


def readiness(inputs: ReadinessInputs, document_type: GeneratedDocumentType) -> DocumentReadiness:
    found = blockers(inputs, document_type)
    return DocumentReadiness(can_generate=not found, blockers=found)
