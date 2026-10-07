"""The stored content of a generated document (V0.8) and its validation report.

A document's `content` is the *assembled* result: the AI's tailored text
(summary, bullets, skill order, letter paragraphs) together with the plain
facts the server copied from the confirmed resume (employers, titles, dates,
degrees, contact details). Each piece of AI-written text carries the
`source_fact_ids` it is based on, resolvable through the document's
`source_facts`, so a reader (or an auditor) can always ask "where does this
come from?".

Like the V0.5 documents, every part forbids unknown keys and is validated on
every write and read of the JSONB column.
"""

from typing import Annotated, Final, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from app.schemas.document_ai import LetterRole
from app.schemas.document_types import DocumentModel, LanguageKey

TAILORED_CV_CONTENT_VERSION: Final = "tailored_cv_content_v1"
COVER_LETTER_CONTENT_VERSION: Final = "cover_letter_content_v1"
VALIDATION_REPORT_VERSION: Final = "document_validation_v1"


# --- Source facts ---------------------------------------------------------------------------


class SourceFact(DocumentModel):
    """One fact the generation was allowed to use: its text, and where it
    came from (`resume_profile:<profile id>/<item id>`, `application`)."""

    text: str = Field(min_length=1, max_length=5000)
    ref: str | None = Field(default=None, max_length=200)


# --- Tailored CV ----------------------------------------------------------------------------


class CvHeader(DocumentModel):
    """Contact details, copied from the confirmed resume's personal information."""

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)


#: Whose words a piece of text is. "ai" text always cites the facts it rests on;
#: "user" text (V0.9: saved from the editor) is the user's own and cites nothing.
TextOrigin = Literal["ai", "user"]


class CvSummary(DocumentModel):
    text: str = Field(min_length=1)
    source_fact_ids: list[str] = Field(default_factory=list)
    origin: TextOrigin = "ai"


class CvBullet(DocumentModel):
    text: str = Field(min_length=1)
    source_fact_ids: list[str] = Field(default_factory=list)
    origin: TextOrigin = "ai"

    @model_validator(mode="after")
    def _ai_text_cites_facts(self) -> Self:
        if self.origin == "ai" and not self.source_fact_ids:
            raise ValueError("a generated bullet cites at least one fact")
        return self


class CvExperience(DocumentModel):
    """A role as the resume states it, with tailored bullets."""

    source_fact_id: str
    company: str | None = None
    job_title: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    date_text: str | None = None
    bullets: list[CvBullet] = Field(default_factory=list)


class CvEducation(DocumentModel):
    source_fact_id: str
    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    date_text: str | None = None


class CvSkill(DocumentModel):
    source_fact_id: str
    name: str = Field(min_length=1)
    emphasized: bool = False


class CvLanguage(DocumentModel):
    source_fact_id: str
    language: str = Field(min_length=1)
    proficiency: str | None = None
    cefr_level: str | None = None


class CvCertification(DocumentModel):
    source_fact_id: str
    name: str = Field(min_length=1)
    issuer: str | None = None
    date: str | None = None


class CvProject(DocumentModel):
    source_fact_id: str
    name: str = Field(min_length=1)
    role: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)


class CvAdditionalInfo(DocumentModel):
    source_fact_id: str
    category: str
    value: str = Field(min_length=1)


class TailoredCvContent(DocumentModel):
    kind: Literal["tailored_cv"] = "tailored_cv"
    schema_version: Literal["tailored_cv_content_v1"] = TAILORED_CV_CONTENT_VERSION
    #: The application language the text is written in.
    language: LanguageKey
    header: CvHeader
    summary: CvSummary
    experience: list[CvExperience] = Field(default_factory=list)
    education: list[CvEducation] = Field(default_factory=list)
    skills: list[CvSkill] = Field(default_factory=list)
    languages: list[CvLanguage] = Field(default_factory=list)
    certifications: list[CvCertification] = Field(default_factory=list)
    projects: list[CvProject] = Field(default_factory=list)
    additional_information: list[CvAdditionalInfo] = Field(default_factory=list)


# --- Cover letter ---------------------------------------------------------------------------


class LetterSender(DocumentModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None


class LetterRecipient(DocumentModel):
    company_name: str | None = None
    job_title: str | None = None


class LetterParagraph(DocumentModel):
    role: LetterRole
    text: str = Field(min_length=1)
    source_fact_ids: list[str] = Field(default_factory=list)
    job_fact_ids: list[str] = Field(default_factory=list)
    origin: TextOrigin = "ai"


class CoverLetterContent(DocumentModel):
    kind: Literal["cover_letter"] = "cover_letter"
    schema_version: Literal["cover_letter_content_v1"] = COVER_LETTER_CONTENT_VERSION
    language: LanguageKey
    sender: LetterSender
    recipient: LetterRecipient
    #: Fixed wording for the language, built from the job title.
    subject: str = Field(min_length=1)
    salutation: str = Field(min_length=1)
    paragraphs: list[LetterParagraph] = Field(min_length=1)
    #: Present only when the user chose to mention their salary expectation:
    #: built from the application's own salary data, never written by the AI.
    salary_statement: str | None = None
    closing_formula: str = Field(min_length=1)
    signature: str | None = None


#: The stored content of either kind, told apart by `kind`.
DocumentContent = Annotated[TailoredCvContent | CoverLetterContent, Field(discriminator="kind")]
CONTENT_ADAPTER: TypeAdapter[TailoredCvContent | CoverLetterContent] = TypeAdapter(DocumentContent)


# --- Validation report ----------------------------------------------------------------------

ValidationStatus = Literal["passed", "failed"]


class Violation(DocumentModel):
    """One reason a generated document was rejected."""

    #: Stable machine-readable code, e.g. UNSUPPORTED_TECHNOLOGY.
    code: str = Field(max_length=60)
    #: Short, user-safe explanation.
    message: str = Field(max_length=600)
    #: Where in the AI output it was found, e.g. "experience[1].bullets[0]".
    location: str = Field(max_length=120)
    #: The offending words, so the user can see what was rejected.
    excerpt: str | None = Field(default=None, max_length=300)


class ValidationReport(DocumentModel):
    schema_version: Literal["document_validation_v1"] = VALIDATION_REPORT_VERSION
    status: ValidationStatus
    #: How many pieces of AI-written text were checked.
    text_units_checked: int = Field(ge=0)
    violations: list[Violation] = Field(default_factory=list)
