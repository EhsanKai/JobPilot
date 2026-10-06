"""Compare the Resume Profile with the user's own (V0.1) profile.

The two are different sources of truth -- "what the user tells JobPilot"
vs. "what the resume says" -- and are never merged here. Disagreements are
only *reported* (both values, `potential_conflict=True`) so the user can
resolve them; neither side is overwritten.

Conflicts and derived metrics are computed on read rather than stored, so
they always reflect the user's current profile and review decisions.
"""

import re
from datetime import UTC, datetime

from app.models.user import User
from app.schemas.resume_profile import (
    DerivedMetrics,
    ProfileConflict,
    ResumeProfileData,
    WorkExperienceItem,
)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _url_key(value: str) -> str:
    return re.sub(r"^(https?://)?(www\.)?", "", value.strip().casefold()).rstrip("/")


def _month_index(partial_date: str, *, is_end: bool) -> tuple[int, bool]:
    """Month index for "YYYY" / "YYYY-MM". Year-only dates use mid-year and
    are reported as approximate rather than assuming January or December."""
    year = int(partial_date[:4])
    if len(partial_date) >= 7:
        return year * 12 + int(partial_date[5:7]) - 1 + (1 if is_end else 0), False
    return year * 12 + 6, True


def compute_derived(data: ResumeProfileData, now: datetime | None = None) -> DerivedMetrics:
    now = now or datetime.now(UTC)
    current_month = now.year * 12 + now.month - 1
    experiences: list[WorkExperienceItem] = data.active_items("work_experience")

    intervals: list[tuple[int, int]] = []
    partial = False
    for exp in experiences:
        if not exp.start_date:
            partial = True
            continue
        start, start_approx = _month_index(exp.start_date, is_end=False)
        if exp.end_date:
            end, end_approx = _month_index(exp.end_date, is_end=True)
        elif exp.is_current:
            end, end_approx = current_month + 1, False
        else:
            # No end date and not marked current: duration unknown.
            partial = True
            continue
        partial = partial or start_approx or end_approx
        if end > start:
            intervals.append((start, end))

    total_months: int | None = None
    if intervals:
        intervals.sort()
        merged: list[list[int]] = [list(intervals[0])]
        for start, end in intervals[1:]:
            if start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        total_months = sum(end - start for start, end in merged)

    current_titles = [exp.job_title for exp in experiences if exp.is_current and exp.job_title]
    return DerivedMetrics(
        total_experience_months=total_months,
        experience_years=round(total_months / 12, 1) if total_months is not None else None,
        experience_is_partial=partial,
        current_job_title=current_titles[0] if current_titles else None,
    )


def detect_conflicts(
    data: ResumeProfileData, user: User, derived: DerivedMetrics
) -> list[ProfileConflict]:
    conflicts: list[ProfileConflict] = []
    personal = {item.field: item.value for item in data.active_items("personal_information")}
    profile = user.profile
    professional = user.professional_profile

    def add(field: str, label: str, profile_value: str, resume_value: str, message: str) -> None:
        conflicts.append(
            ProfileConflict(
                field=field,
                label=label,
                profile_value=profile_value,
                resume_value=resume_value,
                message=message,
            )
        )

    if name := personal.get("full_name"):
        resume_name = _norm(name)
        if not (
            _norm(user.first_name) in resume_name and _norm(user.last_name) in resume_name
        ):
            add(
                "full_name",
                "Name",
                f"{user.first_name} {user.last_name}",
                name,
                "The name on your resume differs from your account name.",
            )

    if (email := personal.get("email")) and _norm(email) != _norm(user.email):
        add(
            "email",
            "Email",
            user.email,
            email,
            "Your resume lists a different email than your JobPilot account.",
        )

    if profile is not None:
        if (phone := personal.get("phone")) and profile.phone:
            resume_digits = re.sub(r"\D", "", phone)[-9:]
            profile_digits = re.sub(r"\D", "", profile.phone)[-9:]
            if resume_digits and profile_digits and resume_digits != profile_digits:
                add("phone", "Phone", profile.phone, phone, "Your phone numbers differ.")
        if (location := personal.get("location")) and profile.location:
            a, b = _norm(location), _norm(profile.location)
            if a not in b and b not in a:
                add(
                    "location",
                    "Location",
                    profile.location,
                    location,
                    "Your resume lists a different location than your profile.",
                )
        if (linkedin := personal.get("linkedin_url")) and profile.linkedin_url:
            if _url_key(linkedin) != _url_key(profile.linkedin_url):
                add(
                    "linkedin_url",
                    "LinkedIn",
                    profile.linkedin_url,
                    linkedin,
                    "Your resume links a different LinkedIn profile.",
                )

    if professional is not None:
        if (
            derived.current_job_title
            and professional.current_job_title
            and _norm(derived.current_job_title) != _norm(professional.current_job_title)
        ):
            add(
                "current_job_title",
                "Current job title",
                professional.current_job_title,
                derived.current_job_title,
                "Your current role on the resume differs from your profile.",
            )
        if (
            professional.years_of_experience is not None
            and derived.total_experience_months is not None
        ):
            resume_years = derived.total_experience_months // 12
            if abs(resume_years - professional.years_of_experience) >= 1:
                qualifier = " (approximate)" if derived.experience_is_partial else ""
                add(
                    "years_of_experience",
                    "Years of experience",
                    str(professional.years_of_experience),
                    f"{resume_years}{qualifier}",
                    "The work history on your resume adds up to a different number of years "
                    "than your profile states.",
                )

    return conflicts
