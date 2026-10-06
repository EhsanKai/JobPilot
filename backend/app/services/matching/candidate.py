"""Candidate snapshot builder (V0.6): V0.1 profile tables + the confirmed
Resume Profile -> an immutable `candidate_profile_versions` row.

The snapshot is the *only* candidate input the matching engine reads, so a
match result can always be reproduced from the exact facts it used. It is
a read model, built on demand:

- **User-declared data** (origin "user"): professional profile, skills,
  languages, industries worked in, and every Job Preference. The editable
  source of truth.
- **The confirmed Resume Profile** (origin "resume"): only items the user
  accepted or edited in the most recently confirmed profile. Nothing from a
  profile still in review, and nothing rejected. The resume is never
  re-parsed here -- this reads the stored V0.2 result.
- Where both state the same fact, **the user's value wins** (the resume
  fills gaps only). Resume values carry an `evidence_ref` to the item.

Every name is normalized through the V0.5.1 taxonomy (skills -> skill keys,
languages -> ISO codes, degrees -> education levels, locations -> city /
country). Values the taxonomy doesn't recognize are kept by name (skills)
or left out where the document needs a key (languages, education levels):
they cannot be compared, so they are never guessed.

Language levels follow the V0.5.1 decision: only an explicitly stated CEFR
level or "native" becomes a level. V0.1's "fluent", "professional", ... and
resume wording like "fließend" stay `unknown`.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.candidate_profile_version import (
    CandidateProfileVersion,
    CandidateProfileVersionReason,
)
from app.models.deal_breaker import DealBreaker
from app.models.industry import UserIndustry
from app.models.job_preferences import JobPreferences
from app.models.language import LanguageProficiency, UserLanguage
from app.models.professional_profile import ProfessionalProfile
from app.models.resume import Resume
from app.models.resume_profile import ResumeProfile, ResumeProfileStatus
from app.models.skill import UserSkill
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.candidate_profile import CandidateProfileDocument
from app.schemas.resume_profile import ResumeProfileData
from app.services.matching.extraction import location_value
from app.services.resume_intelligence.conflicts import compute_derived
from app.services.taxonomy import (
    TAXONOMY_VERSION,
    normalize_education_level,
    normalize_language,
    normalize_language_level,
    normalize_skill,
)

_ACCEPTED = ("accepted", "edited")
_PREFERENCE_SECTIONS = ("location_and_mobility", "preferences", "deal_breakers")


@dataclass(frozen=True)
class CandidateSnapshot:
    """A built (not necessarily stored) snapshot."""

    document: CandidateProfileDocument
    content_hash: str
    sources: list[dict[str, Any]]
    resume_profile_id: uuid.UUID | None


def content_hash(document: CandidateProfileDocument) -> str:
    """SHA-256 of the canonical JSON document (`content_hash`)."""
    encoded = json.dumps(
        document.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def confirmed_resume_profile(db: Session, user: User) -> ResumeProfile | None:
    """The user's most recently confirmed Resume Profile, if any."""
    return db.execute(
        select(ResumeProfile)
        .join(Resume, Resume.id == ResumeProfile.resume_id)
        .where(
            ResumeProfile.user_id == user.id,
            Resume.user_id == user.id,
            ResumeProfile.status == ResumeProfileStatus.CONFIRMED,
        )
        .order_by(ResumeProfile.confirmed_at.desc().nulls_last(), ResumeProfile.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _key(result: Any) -> str | None:
    return result.key if result is not None and result.is_match else None


def _skill_key(*names: str | None) -> str | None:
    for name in names:
        if name and (key := _key(normalize_skill(name))):
            return key
    return None


def _resume_ref(profile_id: uuid.UUID, item: str) -> str:
    return f"resume_profile:{profile_id}/{item}"


def build_snapshot(db: Session, user: User) -> CandidateSnapshot:
    """Assemble the candidate document from the database (no writes)."""
    professional = db.scalar(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    )
    personal = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    preferences = db.scalar(select(JobPreferences).where(JobPreferences.user_id == user.id))
    user_skills = db.scalars(
        select(UserSkill).where(UserSkill.user_id == user.id).order_by(UserSkill.created_at)
    ).all()
    user_languages = db.scalars(
        select(UserLanguage)
        .where(UserLanguage.user_id == user.id)
        .order_by(UserLanguage.created_at)
    ).all()
    industries = db.scalars(
        select(UserIndustry)
        .where(UserIndustry.user_id == user.id)
        .order_by(UserIndustry.created_at)
    ).all()
    deal_breakers = db.scalars(
        select(DealBreaker).where(DealBreaker.user_id == user.id).order_by(DealBreaker.type)
    ).all()
    resume_profile = confirmed_resume_profile(db, user)
    resume = (
        ResumeProfileData.model_validate(resume_profile.reviewed_data or {})
        if resume_profile is not None
        else None
    )

    role: dict[str, Any] = {}
    skills: list[dict[str, Any]] = []
    languages: list[dict[str, Any]] = []
    mobility: dict[str, Any] = {}
    prefs: dict[str, Any] = {}
    education: list[dict[str, Any]] = []
    certifications: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    def source(record_type: str, record: Any = None, origin: str = "user") -> None:
        updated_at = getattr(record, "updated_at", None)
        sources.append(
            {
                "origin": origin,
                "record_type": record_type,
                "record_id": str(record.id) if record is not None else None,
                "record_updated_at": updated_at.isoformat() if updated_at else None,
            }
        )

    # --- User-declared ----------------------------------------------------------------
    if professional is not None:
        source("professional_profile", professional)
        if professional.current_job_title:
            role["current_title"] = {
                "value": professional.current_job_title[:255],
                "origin": "user",
            }
        if professional.seniority_level is not None:
            role["seniority_level"] = {
                "value": professional.seniority_level.value,
                "origin": "user",
            }
        if professional.years_of_experience is not None:
            role["years_of_experience"] = {
                "value": float(min(professional.years_of_experience, 80)),
                "origin": "user",
            }
    if personal is not None:
        source("user_profile", personal)
    if industries:
        # Industries worked in are part of the professional profile.
        source("professional_profile")
        seen: set[str] = set()
        role["industries"] = []
        for link in industries:
            name = link.industry.name.strip()[:255]
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                role["industries"].append(name)

    seen_skill_names: set[str] = set()
    seen_skill_keys: set[str] = set()
    if user_skills:
        source("user_skills")
    for user_skill in user_skills:
        name = user_skill.skill.name.strip()[:255]
        key = _skill_key(name)
        if not name or name.casefold() in seen_skill_names or (key and key in seen_skill_keys):
            continue
        seen_skill_names.add(name.casefold())
        if key:
            seen_skill_keys.add(key)
        entry: dict[str, Any] = {"name": name, "skill_key": key, "origin": "user"}
        if user_skill.proficiency is not None:
            entry["proficiency"] = user_skill.proficiency.value
        if user_skill.years_of_experience is not None:
            entry["years"] = float(min(user_skill.years_of_experience, 80))
        skills.append(entry)

    seen_languages: set[str] = set()
    if user_languages:
        source("user_languages")
    for user_language in user_languages:
        code = _key(normalize_language(user_language.language.name))
        if code is None or code in seen_languages:
            continue
        seen_languages.add(code)
        # Only "native" is an explicit level; V0.1's other proficiency
        # words are not CEFR levels and are never mapped to one.
        level = "native" if user_language.proficiency is LanguageProficiency.NATIVE else "unknown"
        languages.append({"language_key": code, "level": level, "origin": "user"})

    if personal is not None and personal.location:
        mobility["current_location"] = {
            "value": location_value(personal.location),
            "origin": "user",
        }

    target_titles: list[dict[str, Any]] = []
    if preferences is not None:
        source("job_preferences", preferences)
        seen_titles: set[str] = set()
        for target in preferences.job_titles:
            text = target.title.strip()[:255]
            if text and text.casefold() not in seen_titles:
                seen_titles.add(text.casefold())
                target_titles.append({"title": text, "priority": target.priority.value})
        seen_places: set[str] = set()
        preferred_locations = []
        for place in preferences.locations:
            text = place.location.strip()
            if text and text.casefold() not in seen_places:
                seen_places.add(text.casefold())
                preferred_locations.append(location_value(text))
        if preferred_locations:
            mobility["preferred_locations"] = preferred_locations
        if preferences.relocation is not None:
            mobility["relocation"] = preferences.relocation.value
        if preferences.travel_willingness is not None:
            mobility["travel"] = preferences.travel_willingness.value
        prefs = {
            "work_models": sorted({m.work_model.value for m in preferences.work_models}),
            "employment_types": sorted(
                {e.employment_type.value for e in preferences.employment_types}
            ),
            "minimum_salary": preferences.minimum_salary,
            "desired_salary": preferences.desired_salary,
            "currency": preferences.salary_currency,
        }
        seen_industries: set[str] = set()
        prefs["industries"] = []
        for industry in preferences.industries:
            text = industry.industry.strip()[:255]
            if text and text.casefold() not in seen_industries:
                seen_industries.add(text.casefold())
                prefs["industries"].append(text)
    if target_titles:
        role["target_titles"] = target_titles

    if deal_breakers:
        source("deal_breakers")
    breakers = [
        {"type": d.type.value, "value": d.value, "enabled": d.enabled} for d in deal_breakers
    ]

    # --- Confirmed Resume Profile (fills gaps; the user's value wins) -------------------
    if resume is not None and resume_profile is not None:
        source("resume_profile", resume_profile, origin="resume")
        pid = resume_profile.id
        derived = compute_derived(resume)
        experiences = [
            item for item in resume.work_experience if item.review_status in _ACCEPTED
        ]
        if "current_title" not in role and derived.current_job_title:
            current = next((e for e in experiences if e.is_current and e.job_title), None)
            if current is not None and current.job_title:
                role["current_title"] = {
                    "value": current.job_title[:255],
                    "origin": "resume",
                    "evidence_ref": _resume_ref(pid, current.id),
                }
        if "years_of_experience" not in role and derived.experience_years is not None:
            role["years_of_experience"] = {
                "value": float(min(derived.experience_years, 80)),
                "origin": "resume",
                "evidence_ref": _resume_ref(pid, "derived.experience_years"),
            }
        current_title = (role.get("current_title") or {}).get("value", "")
        seen_previous = {current_title.casefold()} if current_title else set()
        previous = []
        for experience in experiences:
            title = (experience.job_title or "").strip()[:255]
            if experience.is_current or not title or title.casefold() in seen_previous:
                continue
            seen_previous.add(title.casefold())
            previous.append(
                {
                    "value": title,
                    "origin": "resume",
                    "evidence_ref": _resume_ref(pid, experience.id),
                }
            )
        if previous:
            role["previous_titles"] = previous

        for skill_item in resume.skills:
            if skill_item.review_status not in _ACCEPTED:
                continue
            name = skill_item.name.strip()[:255]
            key = _skill_key(name, *skill_item.aliases)
            if not name or name.casefold() in seen_skill_names or (key and key in seen_skill_keys):
                continue
            seen_skill_names.add(name.casefold())
            if key:
                seen_skill_keys.add(key)
            entry = {
                "name": name,
                "skill_key": key,
                "proficiency": skill_item.proficiency,
                "origin": "resume",
                "evidence_ref": _resume_ref(pid, skill_item.id),
            }
            if skill_item.category is not None:
                entry["category"] = skill_item.category
            skills.append(entry)

        for language_item in resume.languages:
            if language_item.review_status not in _ACCEPTED:
                continue
            code = _key(normalize_language(language_item.language)) or _key(
                normalize_language(language_item.as_written)
            )
            if code is None or code in seen_languages:
                continue
            seen_languages.add(code)
            stated_level = language_item.cefr_level or _key(
                normalize_language_level(language_item.proficiency)
            )
            languages.append(
                {
                    "language_key": code,
                    "level": stated_level or "unknown",
                    "origin": "resume",
                    "evidence_ref": _resume_ref(pid, language_item.id),
                }
            )

        for education_item in resume.education:
            if education_item.review_status not in _ACCEPTED:
                continue
            entry = {
                "degree": (education_item.degree or "")[:255] or None,
                "degree_level": _key(normalize_education_level(education_item.degree)),
                "field_of_study": (education_item.field_of_study or "")[:255] or None,
                "institution": (education_item.institution or "")[:255] or None,
                "origin": "resume",
                "evidence_ref": _resume_ref(pid, education_item.id),
            }
            if any(entry[k] for k in ("degree", "degree_level", "field_of_study", "institution")):
                education.append(entry)

        for certification in resume.certifications:
            if certification.review_status not in _ACCEPTED:
                continue
            certifications.append(
                {
                    "name": certification.name.strip()[:255],
                    "issuer": (certification.issuer or "")[:255] or None,
                    "date": certification.date,
                    "origin": "resume",
                    "evidence_ref": _resume_ref(pid, certification.id),
                }
            )

        if "current_location" not in mobility:
            location = next(
                (
                    p
                    for p in resume.personal_information
                    if p.field == "location" and p.review_status in _ACCEPTED
                ),
                None,
            )
            if location is not None:
                mobility["current_location"] = {
                    "value": location_value(location.value),
                    "origin": "resume",
                    "evidence_ref": _resume_ref(pid, location.id),
                }

    document = CandidateProfileDocument.model_validate(
        {
            "role": role,
            "skills": skills,
            "languages": languages,
            "location_and_mobility": mobility,
            "preferences": prefs,
            "deal_breakers": breakers,
            "credentials": {"education": education, "certifications": certifications},
            "metadata": {"taxonomy_version": TAXONOMY_VERSION},
        }
    )
    return CandidateSnapshot(
        document=document,
        content_hash=content_hash(document),
        sources=_unique_sources(sources),
        resume_profile_id=resume_profile.id if resume_profile is not None else None,
    )


def _unique_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str | None]] = set()
    result = []
    for entry in sources:
        key = (entry["record_type"], entry["record_id"])
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def _resume_values(value: Any) -> list[Any]:
    """Every resume-derived value in a (dumped) document, in order."""
    if isinstance(value, dict):
        if value.get("origin") == "resume":
            return [value]
        return [v for item in value.values() for v in _resume_values(item)]
    if isinstance(value, list):
        return [v for item in value for v in _resume_values(item)]
    return []


def _reason(
    previous: CandidateProfileVersion | None, document: CandidateProfileDocument
) -> CandidateProfileVersionReason:
    """Why a new version is stored (informational)."""
    if previous is None:
        return CandidateProfileVersionReason.INITIAL
    old_doc = previous.read_document()
    if old_doc.metadata.taxonomy_version != document.metadata.taxonomy_version:
        return CandidateProfileVersionReason.TAXONOMY_UPGRADE
    if old_doc.metadata.schema_version != document.metadata.schema_version:
        return CandidateProfileVersionReason.SCHEMA_UPGRADE
    old, new = old_doc.model_dump(mode="json"), document.model_dump(mode="json")
    if _resume_values(old) != _resume_values(new):
        return CandidateProfileVersionReason.RESUME_CONFIRMED
    changed = {key for key in new if key != "metadata" and old.get(key) != new[key]}
    if "role" in changed:
        old_role = {k: v for k, v in old["role"].items() if k != "target_titles"}
        new_role = {k: v for k, v in new["role"].items() if k != "target_titles"}
        changed.discard("role")
        if old_role != new_role:
            changed.add("role")
        else:
            changed.add("preferences")
    if changed and changed <= set(_PREFERENCE_SECTIONS):
        return CandidateProfileVersionReason.PREFERENCES_UPDATED
    return CandidateProfileVersionReason.PROFILE_UPDATED


def ensure_snapshot(db: Session, user: User) -> CandidateProfileVersion:
    """The stored snapshot for the user's current data, created when the
    content changed. Identical content is always the same version."""
    built = build_snapshot(db, user)
    for _attempt in range(2):
        existing = db.scalar(
            select(CandidateProfileVersion).where(
                CandidateProfileVersion.user_id == user.id,
                CandidateProfileVersion.content_hash == built.content_hash,
            )
        )
        if existing is not None:
            return existing
        latest = db.scalar(
            select(CandidateProfileVersion)
            .where(CandidateProfileVersion.user_id == user.id)
            .order_by(CandidateProfileVersion.version_number.desc())
            .limit(1)
        )
        next_number = (
            db.scalar(
                select(func.max(CandidateProfileVersion.version_number)).where(
                    CandidateProfileVersion.user_id == user.id
                )
            )
            or 0
        ) + 1
        version = CandidateProfileVersion(
            user_id=user.id,
            version_number=next_number,
            content_hash=built.content_hash,
            document=built.document.model_dump(mode="json"),
            sources=built.sources,
            reason=_reason(latest, built.document),
        )
        try:
            with db.begin_nested():
                db.add(version)
            return version
        except IntegrityError:
            # A concurrent request stored the same content (or number) first.
            continue
    raise RuntimeError("could not store the candidate snapshot")
