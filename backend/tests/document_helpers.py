"""Helpers for the V0.8 tailored-document tests.

No test ever calls a real AI API. `FakeDocumentAI` is a deterministic stand-in
for an `AIProvider`: it answers the resume parser with the canned fixture (so
a resume can be analyzed and confirmed through the real API), and answers a
document request by building a *faithful* output from the very context it was
shown -- the same facts, ids and job the real model would get. Tests make it
unfaithful on purpose (`tamper`) to prove that invented claims are rejected.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.provider import AIInvalidResponseError, T, summarize_validation_error
from app.schemas.document_ai import AICoverLetter, AITailoredCV
from tests.application_helpers import API
from tests.resume_helpers import FakeAIProvider

_CONTEXT = re.compile(r"<generation_context>\n(.*)\n</generation_context>", re.S)

Tamper = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]


def context_of(prompt: str) -> dict[str, Any]:
    """The generation context JSON a prompt carries."""
    match = _CONTEXT.search(prompt)
    assert match, "prompt has no <generation_context>"
    return json.loads(match.group(1))  # type: ignore[no-any-return]


#: German renderings of the fixture resume's sentences. A real model translates;
#: the fake only needs the same *claims* in the application language.
GERMAN = {
    "SAP developer with experience in SAP IS-U and ABAP development for utility companies.": (
        "SAP-Entwickler mit Erfahrung in SAP IS-U und ABAP-Entwicklung für Energieversorger."
    ),
    "Developed and maintained SAP IS-U applications for meter-to-cash processes": (
        "Entwicklung und Wartung von SAP IS-U-Anwendungen für Meter-to-Cash-Prozesse"
    ),
    "Implemented ABAP OO enhancements on S/4HANA": (
        "Umsetzung von ABAP-OO-Erweiterungen auf S/4HANA"
    ),
    "Reduced batch runtime by 30% through code optimization": (
        "Reduzierung der Batch-Laufzeit um 30 % durch Code-Optimierung"
    ),
    "Built custom reports and interfaces in SAP-ABAP": (
        "Entwicklung individueller Reports und Schnittstellen in SAP-ABAP"
    ),
    "Worked with SAP BTP for side-by-side extensions": (
        "Arbeit mit SAP BTP für Side-by-Side-Erweiterungen"
    ),
}


def localize(text: str, language: str) -> str:
    return GERMAN.get(text, text) if language == "de" else text


def faithful_cv(context: dict[str, Any]) -> dict[str, Any]:
    """A truthful tailored CV: every bullet restates facts of its own role."""
    language = context["output_language"]["code"]
    facts = context["candidate_facts"]
    summary = facts["summary"]
    summary_ids = [summary["id"]] if summary else [facts["experience"][0]["id"]]
    experience = []
    for entry in facts["experience"]:
        bullets = [
            {"text": localize(fact["text"], language), "source_fact_ids": [fact["id"]]}
            for fact in entry["facts"]
            if fact["kind"] in ("responsibility", "achievement")
        ]
        experience.append({"source_experience_id": entry["id"], "bullets": bullets})
    wanted = {
        item["text"].lower()
        for group in ("technologies", "requirements")
        for item in context["target_job"][group]
    }
    skills = sorted(
        facts["skills"],
        key=lambda skill: not any(w and w in skill["text"].lower() for w in wanted),
    )
    return {
        "summary": {
            "text": localize(summary["text"], language) if summary else "Experienced professional.",
            "source_fact_ids": summary_ids,
        },
        "experience": experience,
        "skills": [
            {
                "source_fact_id": skill["id"],
                "emphasized": any(w and w in skill["text"].lower() for w in wanted),
            }
            for skill in skills
        ],
    }


LETTER_TEXT = {
    "de": {
        "opening": "mit großem Interesse bewerbe ich mich auf die Position {title} bei {company}.",
        "experience": "In meiner Tätigkeit habe ich folgende Aufgaben übernommen: {fact}",
        "skills": "Zu meinen Kenntnissen zählt: {fact}",
        "motivation": "Diese Erfahrung möchte ich in die Stelle einbringen: {fact}",
        "closing": "Ich freue mich auf ein persönliches Gespräch mit Ihnen.",
    },
    "en": {
        "opening": "I am writing to apply for the position of {title} at {company}.",
        "experience": "In my work I have taken on the following tasks: {fact}",
        "skills": "My skills include: {fact}",
        "motivation": "I would like to bring this experience to the role: {fact}",
        "closing": "I look forward to discussing the position with you in person.",
    },
}


def faithful_letter(context: dict[str, Any]) -> dict[str, Any]:
    language = context["output_language"]["code"]
    text = LETTER_TEXT[language]
    facts = context["candidate_facts"]
    job = context["target_job"]
    work = facts["experience"][0]
    responsibility = next(f for f in work["facts"] if f["kind"] == "responsibility")
    skill = facts["skills"][0] if facts["skills"] else None
    paragraphs = [
        {
            "role": "opening",
            "text": text["opening"].format(title=job["title"], company=job["company"]),
            "source_fact_ids": [],
            "job_fact_ids": ["job.title"],
        },
        {
            "role": "experience",
            "text": text["experience"].format(fact=localize(responsibility["text"], language)),
            "source_fact_ids": [work["id"], responsibility["id"]],
            "job_fact_ids": [item["id"] for item in job["responsibilities"][:1]],
        },
    ]
    if skill:
        paragraphs.append(
            {
                "role": "skills",
                "text": text["skills"].format(fact=skill["text"].split(" (")[0]),
                "source_fact_ids": [skill["id"]],
                "job_fact_ids": [],
            }
        )
    paragraphs.append(
        {
            "role": "motivation",
            "text": text["motivation"].format(fact=localize(responsibility["text"], language)),
            "source_fact_ids": [responsibility["id"]],
            "job_fact_ids": [],
        }
    )
    paragraphs.append(
        {"role": "closing", "text": text["closing"], "source_fact_ids": [], "job_fact_ids": []}
    )
    return {"language": language, "paragraphs": paragraphs}


class FakeDocumentAI(FakeAIProvider):
    """The resume parser's fixture for resumes; faithful documents otherwise.

    `cv_tamper` / `letter_tamper` receive the faithful output and the context
    and return a modified output (or None to leave it). `cv_error` /
    `letter_error` make the call raise instead.
    """

    def __init__(self) -> None:
        super().__init__()
        self.cv_tamper: Tamper | None = None
        self.letter_tamper: Tamper | None = None
        self.cv_error: Exception | None = None
        self.letter_error: Exception | None = None
        self.cv_override: dict[str, Any] | None = None
        self.letter_override: dict[str, Any] | None = None
        self.contexts: list[dict[str, Any]] = []

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T:
        if schema not in (AITailoredCV, AICoverLetter):
            return await super().generate_structured(system=system, prompt=prompt, schema=schema)
        self.calls.append({"system": system, "prompt": prompt})
        context = context_of(prompt)
        self.contexts.append(context)
        is_cv = schema is AITailoredCV
        error = self.cv_error if is_cv else self.letter_error
        if error is not None:
            raise error
        override = self.cv_override if is_cv else self.letter_override
        output = (
            override
            if override is not None
            else (faithful_cv(context) if is_cv else faithful_letter(context))
        )
        tamper = self.cv_tamper if is_cv else self.letter_tamper
        if tamper is not None and override is None:
            output = tamper(output, context) or output
        try:
            return schema.model_validate(output)
        except ValidationError as exc:
            raise AIInvalidResponseError(summarize_validation_error(exc)) from None

    @property
    def document_calls(self) -> int:
        return len(self.contexts)


# --- API shortcuts ----------------------------------------------------------------------------


def documents_url(application_id: str) -> str:
    return f"{API}/applications/{application_id}/documents"


def generate(client: TestClient, headers: dict[str, str], application_id: str, kind: str) -> Any:
    """`kind` is "cv" or "cover-letter"."""
    return client.post(f"{documents_url(application_id)}/{kind}", headers=headers)


def listing(client: TestClient, headers: dict[str, str], application_id: str) -> dict[str, Any]:
    response = client.get(documents_url(application_id), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- Pure builders (no database) ----------------------------------------------------------------


def accepted_profile_data(*, reject: tuple[str, ...] = ()) -> Any:
    """The fixture resume as a *confirmed* profile: every item accepted
    (items of the sections named in `reject` are rejected instead)."""
    from app.schemas.resume_profile import ParsedResume
    from app.services.resume_intelligence.validation import build_profile_data
    from tests.resume_helpers import expected_parsed_resume, sample_resume_text

    data = build_profile_data(
        ParsedResume.model_validate(expected_parsed_resume()), sample_resume_text()
    ).data
    for section, item in data.iter_items():
        item.review_status = "rejected" if section in reject else "accepted"
    return data


def context_job(**overrides: Any) -> Any:
    from app.schemas.application_context import ContextJob

    values: dict[str, Any] = {
        "job_id": "00000000-0000-4000-8000-0000000000b1",
        "canonical_url": "https://jobs.example.com/1",
        "title": "Senior Project Manager",
        "company_name": "Example GmbH",
        "location": "Köln",
        "locations": ["Köln"],
        "requirements": ["3+ years of experience", "AWS and Terraform"],
        "responsibilities": ["Plan and run projects"],
        "technologies": ["AWS", "Excel"],
    }
    values.update(overrides)
    return ContextJob.model_validate(values)


def build_context(
    kind: str = "cv",
    *,
    language: str = "de",
    data: Any = None,
    job: Any = None,
    prep: dict[str, Any] | None = None,
    match: Any = None,
) -> Any:
    """The generation context of the fixture resume (`kind`: cv | cover_letter)."""
    from datetime import datetime

    from app.models.generated_document import GeneratedDocumentType
    from app.services.documents.context import Preparation, build_generation_context

    values: dict[str, Any] = {"language": language}
    values.update(prep or {})
    return build_generation_context(
        document_type=(
            GeneratedDocumentType.TAILORED_CV
            if kind == "cv"
            else GeneratedDocumentType.COVER_LETTER
        ),
        language=language,
        resume_profile_id="00000000-0000-4000-8000-00000000e001",
        profile_data=data if data is not None else accepted_profile_data(),
        account_name=("Jane", "Doe"),
        job=job if job is not None else context_job(),
        preparation=Preparation(**values),
        match=match,
        now=datetime(2026, 10, 1),
    )
