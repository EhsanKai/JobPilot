"""The fact catalogue: what a generated document is allowed to say (V0.8).

Facts are built deterministically from the user's *confirmed* Resume Profile
(only items the user accepted or edited -- nothing pending or rejected) and
from the application's own preparation data. Each fact has a short, stable id
("exp1", "exp1.r2", "skill3", "app.notice_period") that the AI cites for
every piece of text it writes, and a text that the truth validation later
compares that text against.

Ids follow the order of the confirmed profile, so the same profile always
yields the same ids and texts. The catalogue is stored with each generated
document (`source_facts`), which keeps every citation resolvable after the
resume has been edited or deleted.

Plain facts that need no wording -- the candidate's name and contact details,
employers, titles, dates, degrees -- are carried next to the facts in
`CandidateFacts` so the server can copy them into the document itself. The AI
never writes them.
"""

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.models.application import NoticePeriodUnit, WorkAuthorization
from app.schemas.document_content import (
    CvAdditionalInfo,
    CvCertification,
    CvEducation,
    CvExperience,
    CvHeader,
    CvLanguage,
    CvProject,
    CvSkill,
    SourceFact,
)
from app.schemas.resume_profile import ResumeProfileData
from app.services.resume_intelligence.conflicts import compute_derived

_ACCEPTED = ("accepted", "edited")

#: Fact id of the resume's own summary.
SUMMARY_FACT_ID = "summary"
YEARS_FACT_ID = "derived.years"


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    #: summary | derived | experience | responsibility | achievement | description |
    #: technologies | education | skill | language | certification | project |
    #: additional | application
    kind: str
    #: The work experience entry a sub-fact belongs to ("exp1"), else None.
    group: str | None = None
    #: Where it came from: `resume_profile:<profile id>/<item id>` or `application`.
    ref: str | None = None


class FactCatalog:
    """Facts by id, in the order they were added."""

    def __init__(self, facts: Iterable[Fact] = ()) -> None:
        self._facts: dict[str, Fact] = {}
        for fact in facts:
            self.add(fact)

    def add(self, fact: Fact) -> None:
        if fact.id in self._facts:
            raise ValueError(f"duplicate fact id {fact.id!r}")
        self._facts[fact.id] = fact

    def __contains__(self, fact_id: object) -> bool:
        return fact_id in self._facts

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts.values())

    def __len__(self) -> int:
        return len(self._facts)

    def get(self, fact_id: str) -> Fact | None:
        return self._facts.get(fact_id)

    def of_kind(self, *kinds: str) -> list[Fact]:
        return [fact for fact in self if fact.kind in kinds]

    def as_source_facts(self) -> dict[str, dict[str, Any]]:
        """The `source_facts` column: id -> {text, ref}."""
        return {
            fact.id: SourceFact(text=fact.text, ref=fact.ref).model_dump(mode="json")
            for fact in self
        }


@dataclass
class CandidateFacts:
    """The confirmed resume, as citeable facts plus the plain data around them."""

    catalog: FactCatalog = field(default_factory=FactCatalog)
    header: CvHeader = field(default_factory=CvHeader)
    summary: Fact | None = None
    #: Work experience entries (without bullets), in resume order.
    experience: list[CvExperience] = field(default_factory=list)
    education: list[CvEducation] = field(default_factory=list)
    skills: list[CvSkill] = field(default_factory=list)
    languages: list[CvLanguage] = field(default_factory=list)
    certifications: list[CvCertification] = field(default_factory=list)
    projects: list[CvProject] = field(default_factory=list)
    additional: list[CvAdditionalInfo] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Nothing to build a document from (no experience, education,
        skills, summary or anything else the user kept)."""
        return len(self.catalog) == 0

    def experience_ids(self) -> list[str]:
        return [entry.source_fact_id for entry in self.experience]


def _dates(
    start: str | None, end: str | None, text: str | None, current: bool | None
) -> str | None:
    if text:
        return text
    if start and (end or current):
        return f"{start} - {end or 'present'}"
    return start or end


def _join(parts: Iterable[str | None], sep: str = ", ") -> str:
    return sep.join(part for part in parts if part)


def _ref(profile_id: object, item_id: str) -> str:
    return f"resume_profile:{profile_id}/{item_id}"


def _kept(items: list[Any]) -> list[Any]:
    return [item for item in items if item.review_status in _ACCEPTED]


def build_candidate_facts(
    profile_id: object, data: ResumeProfileData, *, now: datetime | None = None
) -> CandidateFacts:
    """Facts from the accepted / edited items of a confirmed profile."""
    result = CandidateFacts()
    catalog = result.catalog

    # --- Header (never sent to the AI; copied into the document) ----------------------------
    personal = {item.field: item.value for item in _kept(data.personal_information)}
    result.header = CvHeader(
        full_name=personal.get("full_name"),
        email=personal.get("email"),
        phone=personal.get("phone"),
        location=personal.get("location"),
        links=[
            personal[name]
            for name in ("linkedin_url", "github_url", "portfolio_url")
            if name in personal
        ],
    )

    # --- Summary and derived years ----------------------------------------------------------
    if data.summary is not None and data.summary.review_status in _ACCEPTED:
        result.summary = Fact(
            SUMMARY_FACT_ID,
            data.summary.value,
            "summary",
            ref=_ref(profile_id, data.summary.id),
        )
        catalog.add(result.summary)

    # --- Work experience --------------------------------------------------------------------
    for index, item in enumerate(_kept(data.work_experience), start=1):
        entry_id = f"exp{index}"
        ref = _ref(profile_id, item.id)
        dates = _dates(item.start_date, item.end_date, item.date_text, item.is_current)
        header_text = _join(
            [_join([item.job_title, item.company], " - "), item.location], ", "
        ) + (f" ({dates})" if dates else "")
        if not header_text:
            header_text = "Work experience"
        catalog.add(Fact(entry_id, header_text, "experience", group=entry_id, ref=ref))
        if item.description:
            catalog.add(
                Fact(f"{entry_id}.d", item.description, "description", group=entry_id, ref=ref)
            )
        for n, text in enumerate(item.responsibilities, start=1):
            catalog.add(Fact(f"{entry_id}.r{n}", text, "responsibility", group=entry_id, ref=ref))
        for n, text in enumerate(item.achievements, start=1):
            catalog.add(Fact(f"{entry_id}.a{n}", text, "achievement", group=entry_id, ref=ref))
        if item.technologies:
            catalog.add(
                Fact(
                    f"{entry_id}.t",
                    "Technologies: " + ", ".join(item.technologies),
                    "technologies",
                    group=entry_id,
                    ref=ref,
                )
            )
        result.experience.append(
            CvExperience(
                source_fact_id=entry_id,
                company=item.company,
                job_title=item.job_title,
                location=item.location,
                start_date=item.start_date,
                end_date=item.end_date,
                is_current=item.is_current,
                date_text=item.date_text,
            )
        )

    # --- Education --------------------------------------------------------------------------
    for index, item in enumerate(_kept(data.education), start=1):
        fact_id = f"edu{index}"
        dates = _dates(item.start_date, item.end_date, item.date_text, None)
        text = _join(
            [
                _join([item.degree, item.field_of_study, item.institution, item.location]),
                f"({dates})" if dates else None,
                item.description,
            ],
            " ",
        )
        catalog.add(Fact(fact_id, text or "Education", "education", ref=_ref(profile_id, item.id)))
        result.education.append(
            CvEducation(
                source_fact_id=fact_id,
                institution=item.institution,
                degree=item.degree,
                field_of_study=item.field_of_study,
                location=item.location,
                start_date=item.start_date,
                end_date=item.end_date,
                date_text=item.date_text,
            )
        )

    # --- Skills -----------------------------------------------------------------------------
    for index, item in enumerate(_kept(data.skills), start=1):
        fact_id = f"skill{index}"
        text = item.name
        if item.aliases:
            text += f" (also written: {', '.join(item.aliases)})"
        if item.proficiency != "unknown":
            text += f" - {item.proficiency}"
        catalog.add(Fact(fact_id, text, "skill", ref=_ref(profile_id, item.id)))
        result.skills.append(CvSkill(source_fact_id=fact_id, name=item.name))

    # --- Languages --------------------------------------------------------------------------
    for index, item in enumerate(_kept(data.languages), start=1):
        fact_id = f"lang{index}"
        level = item.proficiency or item.cefr_level
        if item.cefr_level and item.proficiency and item.cefr_level not in item.proficiency:
            level = f"{item.proficiency} ({item.cefr_level})"
        text = f"{item.language} - {level}" if level else item.language
        catalog.add(Fact(fact_id, text, "language", ref=_ref(profile_id, item.id)))
        result.languages.append(
            CvLanguage(
                source_fact_id=fact_id,
                language=item.language,
                proficiency=item.proficiency,
                cefr_level=item.cefr_level,
            )
        )

    # --- Certifications ---------------------------------------------------------------------
    for index, item in enumerate(_kept(data.certifications), start=1):
        fact_id = f"cert{index}"
        text = _join([item.name, item.issuer, item.date])
        catalog.add(Fact(fact_id, text, "certification", ref=_ref(profile_id, item.id)))
        result.certifications.append(
            CvCertification(
                source_fact_id=fact_id, name=item.name, issuer=item.issuer, date=item.date
            )
        )

    # --- Projects ---------------------------------------------------------------------------
    for index, item in enumerate(_kept(data.projects), start=1):
        fact_id = f"proj{index}"
        dates = _dates(item.start_date, item.end_date, item.date_text, None)
        text = _join(
            [
                item.name,
                item.role,
                item.description,
                "Technologies: " + ", ".join(item.technologies) if item.technologies else None,
                f"({dates})" if dates else None,
            ],
            ". ",
        )
        catalog.add(Fact(fact_id, text, "project", ref=_ref(profile_id, item.id)))
        result.projects.append(
            CvProject(
                source_fact_id=fact_id,
                name=item.name,
                role=item.role,
                description=item.description,
                technologies=list(item.technologies),
            )
        )

    # --- Additional information -------------------------------------------------------------
    for index, item in enumerate(_kept(data.additional_information), start=1):
        fact_id = f"add{index}"
        catalog.add(Fact(fact_id, item.value, "additional", ref=_ref(profile_id, item.id)))
        result.additional.append(
            CvAdditionalInfo(source_fact_id=fact_id, category=item.category, value=item.value)
        )

    # --- Derived: total experience, only when the dates fully support it --------------------
    derived = compute_derived(data, now)
    if (
        derived.total_experience_months is not None
        and not derived.experience_is_partial
        and derived.total_experience_months >= 12
    ):
        years = math.floor(derived.total_experience_months / 12)
        catalog.add(
            Fact(
                YEARS_FACT_ID,
                f"{years} years of professional experience in total "
                "(calculated from the dates of the roles in the resume)",
                "derived",
                ref="derived",
            )
        )
    return result


# --- Application facts (cover letter) ----------------------------------------------------------

_NOTICE_UNIT_TEXT = {
    NoticePeriodUnit.DAYS: "days",
    NoticePeriodUnit.WEEKS: "weeks",
    NoticePeriodUnit.MONTHS: "months",
}


def application_facts(
    *,
    available_from: date | None,
    notice_period: tuple[int, NoticePeriodUnit] | None,
    work_authorization: WorkAuthorization | None,
) -> list[Fact]:
    """What the user stated for this application that a letter may mention.
    Salary is deliberately not a fact: it is never written by the AI."""
    facts: list[Fact] = []
    if available_from is not None:
        facts.append(
            Fact(
                "app.available_from",
                f"Available from {available_from.isoformat()}",
                "application",
                ref="application",
            )
        )
    if notice_period is not None:
        value, unit = notice_period
        text = (
            "Available immediately (no notice period)"
            if value == 0
            else f"Notice period: {value} {_NOTICE_UNIT_TEXT[unit]}"
        )
        facts.append(Fact("app.notice_period", text, "application", ref="application"))
    if work_authorization is WorkAuthorization.AUTHORIZED:
        facts.append(
            Fact(
                "app.work_authorization",
                "The candidate states they are authorized to work for this job",
                "application",
                ref="application",
            )
        )
    elif work_authorization is WorkAuthorization.REQUIRES_SPONSORSHIP:
        facts.append(
            Fact(
                "app.work_authorization",
                "The candidate states they would need sponsorship to work for this job",
                "application",
                ref="application",
            )
        )
    return facts
