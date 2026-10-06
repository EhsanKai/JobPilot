"""Assembling the final document from validated AI output (V0.8).

The AI contributes text; the server contributes facts. Employers, job
titles, locations, dates, degrees, skill names, languages, certifications,
projects and contact details are copied from the confirmed resume (they were
read from `CandidateFacts`, never from the AI), and the fixed wording of a
letter -- subject, salutation, closing formula, salary sentence -- comes from
`context.py`. Only after `validation.py` has passed does anything get
assembled, and the result is validated once more as a stored-content model.
"""

from app.schemas.document_ai import AICoverLetter, AITailoredCV
from app.schemas.document_content import (
    CoverLetterContent,
    CvBullet,
    CvExperience,
    CvSkill,
    CvSummary,
    LetterParagraph,
    LetterRecipient,
    TailoredCvContent,
)
from app.services.documents.context import (
    LETTER_CLOSING,
    LETTER_SALUTATION,
    LETTER_SUBJECT,
    LETTER_SUBJECT_GENERIC,
    GenerationContext,
)


def _unique(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def assemble_cv(output: AITailoredCV, context: GenerationContext) -> TailoredCvContent:
    candidate = context.candidate
    by_id = {entry.source_fact_id: entry for entry in candidate.experience}
    experience: list[CvExperience] = []
    for entry in output.experience:
        base = by_id[entry.source_experience_id]
        experience.append(
            base.model_copy(
                update={
                    "bullets": [
                        CvBullet(text=b.text, source_fact_ids=_unique(b.source_fact_ids))
                        for b in entry.bullets
                    ]
                }
            )
        )

    skills_by_id = {skill.source_fact_id: skill for skill in candidate.skills}
    skills: list[CvSkill] = [
        skills_by_id[s.source_fact_id].model_copy(update={"emphasized": s.emphasized})
        for s in output.skills
    ]
    return TailoredCvContent(
        language=context.language,
        header=candidate.header,
        summary=CvSummary(
            text=output.summary.text, source_fact_ids=_unique(output.summary.source_fact_ids)
        ),
        experience=experience,
        education=candidate.education,
        skills=skills,
        languages=candidate.languages,
        certifications=candidate.certifications,
        projects=candidate.projects,
        additional_information=candidate.additional,
    )


def assemble_letter(output: AICoverLetter, context: GenerationContext) -> CoverLetterContent:
    language = context.language
    title = context.job_title.strip()
    subject = (
        LETTER_SUBJECT[language].format(title=title) if title else LETTER_SUBJECT_GENERIC[language]
    )
    return CoverLetterContent(
        language=language,
        sender=context.sender,
        recipient=LetterRecipient(
            company_name=context.company_name or None, job_title=title or None
        ),
        subject=subject,
        salutation=LETTER_SALUTATION[language],
        paragraphs=[
            LetterParagraph(
                role=p.role,
                text=p.text,
                source_fact_ids=_unique(p.source_fact_ids),
                job_fact_ids=_unique(p.job_fact_ids),
            )
            for p in output.paragraphs
        ],
        salary_statement=context.salary_statement,
        closing_formula=LETTER_CLOSING[language],
        signature=context.sender.full_name,
    )
