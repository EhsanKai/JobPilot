"""The deterministic generation context (V0.8).

Everything a generation depends on, assembled explicitly from named inputs --
never "the database":

    candidate    the confirmed resume's facts            (`facts.build_candidate_facts`)
    job          the application's context snapshot       (immutable; what the user saw)
    application  language, availability, notice period, salary decision
    match        the current V0.6 result, as guidance (letter only)

`build_generation_context` is a pure function of those inputs: the same
inputs always give the same prompt payload, the same fact ids and the same
`input_hash`. It is what the AI is shown (`payload`), and the validator and
the assembler read the same object, so the three can never disagree about
which facts exist.

What the AI does *not* get: contact details (they are copied into the
document by the server), notes, the salary figure, other applications, other
resumes, the user's profile or anything else in the database.

`input_hash` covers the payload and the server-side inputs of the final
document (header, subject, salary sentence...). It is stored with the
document; when the hash of today's context differs, the document is
*outdated* -- the resume, the application data or the match changed since.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.models.application import NoticePeriodUnit, WorkAuthorization
from app.models.generated_document import GeneratedDocumentType
from app.schemas.application_context import ContextJob
from app.schemas.document_content import LetterSender
from app.schemas.resume_profile import ResumeProfileData
from app.services.documents.facts import (
    CandidateFacts,
    Fact,
    FactCatalog,
    application_facts,
    build_candidate_facts,
)

CONTEXT_VERSION = "generation_context_v1"

#: Languages documents can be generated in. Application languages are
#: configuration (`SUPPORTED_APPLICATION_LANGUAGES`); the fixed wording of a
#: letter (subject, salutation, closing) must exist for each one.
GENERATION_LANGUAGES: tuple[str, ...] = ("de", "en")
LANGUAGE_NAMES = {"de": "German", "en": "English"}

# Bounds on what is copied from the job, so a very long posting cannot
# swamp the prompt. Documented in docs/tailored-documents.md.
_MAX_JOB_ITEMS = 15
_MAX_JOB_TECHNOLOGIES = 30
_MAX_JOB_ITEM_CHARS = 300
_MAX_JOB_DESCRIPTION_CHARS = 3000
_MAX_MATCH_ITEMS = 8


@dataclass(frozen=True)
class Preparation:
    """The application's own data a document may depend on."""

    language: str | None
    salary_expectation: Decimal | None = None
    salary_currency: str | None = None
    mention_salary_in_letter: bool | None = None
    available_from: date | None = None
    notice_period: tuple[int, NoticePeriodUnit] | None = None
    work_authorization: WorkAuthorization | None = None


@dataclass(frozen=True)
class MatchGuidance:
    """What the current match says, as hints about what to emphasize and what
    never to claim. Hints only: a claim about the candidate must still cite a
    resume fact, whatever the match says."""

    strong_matches: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenerationContext:
    document_type: GeneratedDocumentType
    language: str
    candidate: CandidateFacts
    #: The facts the AI may cite: the candidate's, plus (letter) the application's.
    catalog: FactCatalog
    #: Ids of the job facts ("job.title", "job.resp1"...). Referable, but never
    #: support for a claim about the candidate.
    job_fact_ids: frozenset[str]
    #: The job's own title (in the forms a text may repeat it) and its company
    #: and location names: a letter may refer to them without claiming
    #: anything (see `claims.mask_job_references`). A CV is never masked.
    title_variants: tuple[str, ...]
    name_variants: tuple[str, ...]
    job_title: str
    company_name: str
    #: What the AI is shown.
    payload: dict[str, Any]
    sender: LetterSender
    #: The fixed salary sentence of a letter, only when the user chose to mention it.
    salary_statement: str | None
    input_hash: str
    #: Fixed facts of the final document that the AI does not write.
    assembly: dict[str, Any] = field(default_factory=dict)


# --- Formatting ---------------------------------------------------------------------------------


def format_amount(amount: Decimal, language: str) -> str:
    """78000 -> "78.000" (de) / "78,000" (en); cents only when present."""
    whole, _, cents = f"{amount:.2f}".partition(".")
    grouped = f"{int(whole):,}"
    has_cents = cents != "00"
    if language == "de":
        text = grouped.replace(",", ".")
        return f"{text},{cents}" if has_cents else text
    return f"{grouped}.{cents}" if has_cents else grouped


SALARY_SENTENCE = {
    "de": "Meine Gehaltsvorstellung liegt bei {amount} {currency} brutto pro Jahr.",
    "en": "My salary expectation is {amount} {currency} gross per year.",
}
LETTER_SUBJECT = {
    "de": "Bewerbung als {title}",
    "en": "Application for the position of {title}",
}
LETTER_SUBJECT_GENERIC = {
    "de": "Bewerbung auf die ausgeschriebene Position",
    "en": "Application for the advertised position",
}
LETTER_SALUTATION = {"de": "Sehr geehrte Damen und Herren,", "en": "Dear Hiring Team,"}
LETTER_CLOSING = {"de": "Mit freundlichen Grüßen", "en": "Kind regards"}


def salary_statement(preparation: Preparation) -> str | None:
    """The sentence stating the salary expectation, or None. Only when the
    user explicitly chose to mention it and the application has one: never
    assumed, never invented."""
    if (
        preparation.mention_salary_in_letter is not True
        or preparation.salary_expectation is None
        or preparation.salary_currency is None
        or preparation.language not in SALARY_SENTENCE
    ):
        return None
    return SALARY_SENTENCE[preparation.language].format(
        amount=format_amount(preparation.salary_expectation, preparation.language),
        currency=preparation.salary_currency,
    )


# --- The job ------------------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _job_items(prefix: str, values: list[str], limit: int) -> list[dict[str, str]]:
    cleaned = [_clip(value, _MAX_JOB_ITEM_CHARS) for value in values if value and value.strip()]
    return [{"id": f"{prefix}{n}", "text": text} for n, text in enumerate(cleaned[:limit], start=1)]


def _job_payload(job: ContextJob) -> tuple[dict[str, Any], frozenset[str]]:
    responsibilities = _job_items("job.resp", job.responsibilities, _MAX_JOB_ITEMS)
    requirements = _job_items("job.req", job.requirements, _MAX_JOB_ITEMS)
    technologies = _job_items("job.tech", job.technologies, _MAX_JOB_TECHNOLOGIES)
    payload: dict[str, Any] = {
        "title": job.title,
        "company": job.company_name,
        "location": job.location,
        "employment_type": job.employment_type.value if job.employment_type else None,
        "work_model": job.work_model.value if job.work_model else None,
        "seniority": job.seniority.value if job.seniority else None,
        "industry": job.industry,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "technologies": technologies,
    }
    if not responsibilities and not requirements and job.description:
        payload["description_excerpt"] = _clip(job.description, _MAX_JOB_DESCRIPTION_CHARS)
    ids = {"job.title", "job.company", "job.location"}
    for group in (responsibilities, requirements, technologies):
        ids.update(item["id"] for item in group)
    return payload, frozenset(ids)


def _mask_variants(value: str | None) -> list[str]:
    """The job's own name in the forms a text may repeat: as given, without a
    trailing "(m/w/d)", and a company without its legal form."""
    if not value or not value.strip():
        return []
    variants = [value.strip()]
    without_brackets = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", value).strip()
    if without_brackets:
        variants.append(without_brackets)
    without_legal = re.sub(
        r"\s+(GmbH(?:\s*&\s*Co\.?\s*KG)?|AG|SE|KG|UG|Inc\.?|Ltd\.?|LLC|Corp\.?|plc)$",
        "",
        without_brackets or value,
        flags=re.IGNORECASE,
    ).strip()
    if without_legal:
        variants.append(without_legal)
    return list(dict.fromkeys(variants))


#: The forms in which a text may repeat a job's own title / company / place; shared
#: with the V0.9 validators so there is one definition.
mask_variants = _mask_variants


# --- The candidate ------------------------------------------------------------------------------


def _candidate_payload(candidate: CandidateFacts) -> dict[str, Any]:
    catalog = candidate.catalog

    def entries(*kinds: str) -> list[dict[str, str]]:
        return [{"id": f.id, "text": f.text} for f in catalog.of_kind(*kinds)]

    experience = []
    for entry in candidate.experience:
        facts = [f for f in catalog if f.group == entry.source_fact_id]
        header, *rest = facts
        experience.append(
            {
                "id": entry.source_fact_id,
                "position": header.text,
                "facts": [{"id": f.id, "kind": f.kind, "text": f.text} for f in rest],
            }
        )
    return {
        "summary": (
            {"id": candidate.summary.id, "text": candidate.summary.text}
            if candidate.summary
            else None
        ),
        "derived": entries("derived"),
        "experience": experience,
        "education": entries("education"),
        "skills": entries("skill"),
        "languages": entries("language"),
        "certifications": entries("certification"),
        "projects": entries("project"),
        "additional_information": entries("additional"),
    }


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


# --- Assembly -----------------------------------------------------------------------------------


def build_generation_context(
    *,
    document_type: GeneratedDocumentType,
    language: str,
    resume_profile_id: object,
    profile_data: ResumeProfileData,
    account_name: tuple[str, str],
    job: ContextJob,
    preparation: Preparation,
    match: MatchGuidance | None = None,
    now: datetime | None = None,
) -> GenerationContext:
    """The context of one generation. Pure: no database, no clock beyond `now`."""
    is_letter = document_type is GeneratedDocumentType.COVER_LETTER
    candidate = build_candidate_facts(resume_profile_id, profile_data, now=now)

    catalog = FactCatalog(candidate.catalog)
    app_facts: list[Fact] = []
    if is_letter:
        app_facts = application_facts(
            available_from=preparation.available_from,
            notice_period=preparation.notice_period,
            work_authorization=preparation.work_authorization,
        )
        for fact in app_facts:
            catalog.add(fact)

    job_payload, job_fact_ids = _job_payload(job)
    payload: dict[str, Any] = {
        "context_version": CONTEXT_VERSION,
        "task": document_type.value,
        "output_language": {"code": language, "name": LANGUAGE_NAMES.get(language, language)},
        "target_job": job_payload,
        "candidate_facts": _candidate_payload(candidate),
    }
    if is_letter:
        payload["application_facts"] = [{"id": f.id, "text": f.text} for f in app_facts]
        if match is not None and (match.strong_matches or match.gaps):
            payload["match_guidance"] = {
                "job_requirements_the_candidate_meets": list(
                    match.strong_matches[:_MAX_MATCH_ITEMS]
                ),
                "job_requirements_the_candidate_does_not_meet_never_claim_them": list(
                    match.gaps[:_MAX_MATCH_ITEMS]
                ),
            }

    title_variants = tuple(_mask_variants(job.title))
    name_variants = tuple(
        dict.fromkeys(
            [
                *_mask_variants(job.company_name),
                *_mask_variants(job.location),
                *(variant for place in job.locations for variant in _mask_variants(place)),
            ]
        )
    )

    header = candidate.header
    sender = LetterSender(
        full_name=header.full_name or " ".join(part for part in account_name if part) or None,
        email=header.email,
        phone=header.phone,
        location=header.location,
    )
    statement = salary_statement(preparation) if is_letter else None

    assembly: dict[str, Any] = {
        "header": header.model_dump(mode="json"),
        "sender": sender.model_dump(mode="json"),
        "company": job.company_name,
        "job_title": job.title,
        "salary_statement": statement,
        "fixed_wording": {
            "subject": LETTER_SUBJECT.get(language),
            "salutation": LETTER_SALUTATION.get(language),
            "closing": LETTER_CLOSING.get(language),
        }
        if is_letter
        else None,
        "experience": [e.model_dump(mode="json") for e in candidate.experience],
        "education": [e.model_dump(mode="json") for e in candidate.education],
        "languages": [e.model_dump(mode="json") for e in candidate.languages],
        "certifications": [e.model_dump(mode="json") for e in candidate.certifications],
        "projects": [e.model_dump(mode="json") for e in candidate.projects],
        "additional": [e.model_dump(mode="json") for e in candidate.additional],
        "skills": [s.model_dump(mode="json") for s in candidate.skills],
    }
    return GenerationContext(
        document_type=document_type,
        language=language,
        candidate=candidate,
        catalog=catalog,
        job_fact_ids=job_fact_ids,
        title_variants=title_variants,
        name_variants=name_variants,
        job_title=job.title,
        company_name=job.company_name,
        payload=payload,
        sender=sender,
        salary_statement=statement,
        input_hash=canonical_hash({"payload": payload, "assembly": assembly}),
        assembly=assembly,
    )
