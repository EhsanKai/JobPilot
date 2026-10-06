"""The user's edits of a generated document (V0.9).

An edit never changes a version: it is saved as a *new* version whose origin is
`user_edited` and whose `base_document_id` names the version it started from.
Everything the user did not touch is carried over unchanged (with its source
facts); text the user wrote or changed is marked `origin: "user"`, cites no facts
(they are the user's own words) and is never presented as checked.

What is editable is the text: a CV's summary, bullets and contact header; a
letter's subject, salutation, paragraphs, salary sentence, closing formula and
signature. Employers, titles, dates, degrees and the other plain facts of a CV are
the resume's -- they are changed in the resume review, so the next generation
and the documents agree. An edit that changes nothing is refused rather than saved
as a duplicate version.
"""

from app.core.errors import AppError
from app.schemas.document_content import (
    CoverLetterContent,
    CvBullet,
    CvHeader,
    CvSummary,
    LetterParagraph,
    TailoredCvContent,
)
from app.schemas.generated_documents import CvEditRequest, LetterEditRequest

NO_CHANGES = AppError("NO_CHANGES", "Nothing was changed, so there is nothing to save.", 409)


def _blank_to_none(value: str | None) -> str | None:
    return value if value else None


def apply_cv_edit(base: TailoredCvContent, edit: CvEditRequest) -> TailoredCvContent:
    summary = (
        base.summary
        if edit.summary == base.summary.text
        else CvSummary(text=edit.summary, source_fact_ids=[], origin="user")
    )
    header = CvHeader(
        full_name=_blank_to_none(edit.header.full_name),
        email=_blank_to_none(edit.header.email),
        phone=_blank_to_none(edit.header.phone),
        location=_blank_to_none(edit.header.location),
        links=[link for link in edit.header.links if link],
    )
    edited = {entry.source_fact_id: entry.bullets for entry in edit.experience}
    known = {entry.source_fact_id for entry in base.experience}
    unknown = set(edited) - known
    if unknown:
        raise AppError(
            "UNKNOWN_EXPERIENCE",
            "The edit mentions a role that is not in this CV.",
            422,
            {"source_fact_ids": sorted(unknown)},
        )
    experience = []
    for entry in base.experience:
        if entry.source_fact_id not in edited:
            experience.append(entry)
            continue
        old = {bullet.text: bullet for bullet in entry.bullets}
        bullets = [
            old.get(text) or CvBullet(text=text, source_fact_ids=[], origin="user")
            for text in edited[entry.source_fact_id]
        ]
        experience.append(entry.model_copy(update={"bullets": bullets}))
    result = base.model_copy(
        update={"summary": summary, "header": header, "experience": experience}
    )
    if result.model_dump() == base.model_dump():
        raise NO_CHANGES
    return result


def apply_letter_edit(base: CoverLetterContent, edit: LetterEditRequest) -> CoverLetterContent:
    paragraphs: list[LetterParagraph] = []
    for index, new in enumerate(edit.paragraphs):
        old = base.paragraphs[index] if index < len(base.paragraphs) else None
        if old is not None and old.role == new.role and old.text == new.text:
            paragraphs.append(old)
        else:
            paragraphs.append(LetterParagraph(role=new.role, text=new.text, origin="user"))
    result = base.model_copy(
        update={
            "subject": edit.subject,
            "salutation": edit.salutation,
            "paragraphs": paragraphs,
            "salary_statement": _blank_to_none(edit.salary_statement),
            "closing_formula": edit.closing_formula,
            "signature": _blank_to_none(edit.signature),
        }
    )
    if result.model_dump() == base.model_dump():
        raise NO_CHANGES
    return result
