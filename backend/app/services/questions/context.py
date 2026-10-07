"""The generation context of an AI answer to an application question (V0.9).

Built the way V0.8 builds a document's context -- from named, trusted inputs,
by a pure function -- and in fact *on top of it*: the candidate facts (the
confirmed resume), the application's facts (availability, notice period, work
authorization), the target job and the id catalogue come from
`documents.context.build_generation_context`, so a question answer cites
exactly the fact ids a letter does and is checked against the same facts.

What the AI is shown: the question (text, type, limit, options), the output
language, the target job and those facts. What it is *not* shown: the salary
figure, contact details, notes, other applications, other resumes, the profile,
preferences, and every earlier answer or AI output -- so nothing generated can
come back as "fact".

`input_hash` covers everything the AI sees: it is stored with the attempt.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.generated_document import GeneratedDocumentType
from app.schemas.application_context import ContextJob
from app.schemas.resume_profile import ResumeProfileData
from app.services.documents.context import (
    GenerationContext,
    Preparation,
    build_generation_context,
    canonical_hash,
)
from app.services.questions.deterministic import QuestionShape

CONTEXT_VERSION = "question_context_v1"


@dataclass(frozen=True)
class QuestionContext:
    #: The V0.8 context the facts, the catalogue and the job references come from.
    generation: GenerationContext
    question: QuestionShape
    required: bool
    #: What the AI is shown.
    payload: dict[str, Any]
    input_hash: str

    @property
    def language(self) -> str:
        return self.generation.language


def build_question_context(
    *,
    question: QuestionShape,
    required: bool,
    language: str,
    resume_profile_id: object,
    profile_data: ResumeProfileData,
    account_name: tuple[str, str],
    job: ContextJob,
    preparation: Preparation,
    now: datetime | None = None,
) -> QuestionContext:
    """The context of one answer. Pure: no database, no clock beyond `now`."""
    generation = build_generation_context(
        # The letter context is the one that carries the application's facts.
        document_type=GeneratedDocumentType.COVER_LETTER,
        language=language,
        resume_profile_id=resume_profile_id,
        profile_data=profile_data,
        account_name=account_name,
        job=job,
        preparation=preparation,
        match=None,
        now=now,
    )
    base = generation.payload
    payload: dict[str, Any] = {
        "context_version": CONTEXT_VERSION,
        "task": "application_question_answer",
        "output_language": base["output_language"],
        "question": {
            "text": question.text,
            "type": question.question_type.value,
            "required": required,
            "max_length": question.max_length,
            "options": question.options,
        },
        "target_job": base["target_job"],
        "candidate_facts": base["candidate_facts"],
        "application_facts": base["application_facts"],
    }
    return QuestionContext(
        generation=generation,
        question=question,
        required=required,
        payload=payload,
        input_hash=canonical_hash(payload),
    )
