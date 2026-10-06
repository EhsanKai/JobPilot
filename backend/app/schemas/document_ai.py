"""What the AI must return for a tailored document (V0.8).

Deliberately small. The AI writes *only* the parts that tailoring is about --
a summary, the wording of experience bullets, the order and emphasis of
skills, and the body paragraphs of a letter -- and every piece of text names
the resume facts (`source_fact_ids`) it is based on. Everything that is a
plain fact (names, employers, job titles, dates, degrees, skill names,
languages, certifications, contact details) is never written by the AI: the
server copies it from the confirmed resume (`services/documents/assembly.py`).
The model therefore cannot invent an employer, a degree or a date: there is
no field for it.

Like `ParsedResume`, the field descriptions double as instructions, since
the JSON schema is sent to the provider. Responses are validated against
these models before anything else happens, and then checked for truthfulness
by `services/documents/validation.py`.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Content limits. They bound the stored document and the validator's work;
#: output beyond them is invalid output, never silently truncated.
MAX_SUMMARY_CHARS = 900
MAX_BULLET_CHARS = 500
MAX_BULLETS_PER_EXPERIENCE = 8
MAX_SKILLS = 40
MAX_PARAGRAPH_CHARS = 1600
MAX_PARAGRAPHS = 8
MAX_FACT_IDS = 12

LetterRole = Literal["opening", "role_fit", "experience", "skills", "motivation", "closing"]


class _AIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AITailoredSummary(_AIModel):
    text: str = Field(
        description=(
            "2-4 sentences. Emphasize the experience in the candidate facts that is relevant "
            "to the target job. Only state what the cited facts support."
        ),
        max_length=MAX_SUMMARY_CHARS,
    )
    source_fact_ids: list[str] = Field(
        description="Ids of EVERY candidate fact the text is based on.", max_length=MAX_FACT_IDS
    )


class AITailoredBullet(_AIModel):
    text: str = Field(
        description=(
            "One concise bullet about this role, rewritten from the cited facts for clarity "
            "and relevance. Add nothing the cited facts do not state: no new tools, numbers, "
            "results, scope or responsibilities."
        ),
        max_length=MAX_BULLET_CHARS,
    )
    source_fact_ids: list[str] = Field(
        description="Ids of EVERY fact of THIS role that the bullet is based on.",
        max_length=MAX_FACT_IDS,
    )


class AITailoredExperience(_AIModel):
    source_experience_id: str = Field(
        description="Id of the work experience entry, e.g. 'exp1'. Use every entry exactly once."
    )
    bullets: list[AITailoredBullet] = Field(
        default_factory=list, max_length=MAX_BULLETS_PER_EXPERIENCE
    )


class AITailoredSkill(_AIModel):
    source_fact_id: str = Field(description="Id of a skill fact, e.g. 'skill3'. Never a new skill.")
    emphasized: bool = Field(
        default=False, description="true if the skill is especially relevant to the target job."
    )


class AITailoredCV(_AIModel):
    """Root of the tailored CV contract."""

    summary: AITailoredSummary
    experience: list[AITailoredExperience] = Field(
        description=(
            "Every work experience entry exactly once, most relevant to the target job first."
        )
    )
    skills: list[AITailoredSkill] = Field(
        default_factory=list,
        description="Skills from the candidate facts, most relevant to the target job first.",
        max_length=MAX_SKILLS,
    )


class AILetterParagraph(_AIModel):
    role: LetterRole = Field(
        description=(
            "'opening' (first), 'role_fit', 'experience', 'skills', 'motivation' or 'closing' "
            "(last)."
        )
    )
    text: str = Field(max_length=MAX_PARAGRAPH_CHARS)
    source_fact_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of EVERY candidate or application fact the paragraph is based on. Required "
            "for 'experience', 'skills' and 'motivation'."
        ),
        max_length=MAX_FACT_IDS,
    )
    job_fact_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the job facts (job.*) the paragraph refers to, if any.",
        max_length=MAX_FACT_IDS,
    )


class AICoverLetter(_AIModel):
    """Root of the cover letter (Anschreiben) contract: the body only."""

    language: str = Field(description="ISO 639-1 code of the language the letter is written in.")
    paragraphs: list[AILetterParagraph] = Field(max_length=MAX_PARAGRAPHS)
