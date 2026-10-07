"""What JobPilot stores about a user, table by table -- the single source of truth for the
data export, the account deletion report and the "My data" overview (V0.13).

Every table is either USER-OWNED (listed in a category below: exported, and deleted with
the account) or SHARED (public job data and name catalogs that belong to no user: neither
exported raw nor deleted). tests/test_privacy.py fails if a table is in neither list, so a
new table cannot silently escape the export or the deletion.
"""

from dataclasses import dataclass
from typing import Final

from app.core.config import settings


@dataclass(frozen=True)
class DataCategory:
    key: str
    title: str
    #: What it is used for, in plain words.
    purpose: str
    #: How long it is kept. Nothing is deleted automatically yet; where a fixed period
    #: would need a product / legal decision, that is said explicitly (docs/privacy.md).
    retention: str
    tables: tuple[str, ...]


_UNTIL_DELETED = (
    "Kept until you delete it or your account. No automatic retention period has been "
    "decided yet (open product/legal decision)."
)

CATEGORIES: Final[tuple[DataCategory, ...]] = (
    DataCategory(
        "account",
        "Account",
        "Signing in and identifying your JobPilot account.",
        "Until you delete your account.",
        ("users",),
    ),
    DataCategory(
        "profile",
        "Profile",
        "Your personal and professional profile, skills and languages, used for matching "
        "and for your application documents.",
        _UNTIL_DELETED,
        (
            "user_profiles",
            "professional_profiles",
            "user_industries",
            "user_skills",
            "user_languages",
        ),
    ),
    DataCategory(
        "job_preferences",
        "Job preferences",
        "What you are looking for (titles, locations, salary, deal breakers), used to find "
        "and rank jobs.",
        _UNTIL_DELETED,
        (
            "job_preferences",
            "job_preference_job_titles",
            "job_preference_locations",
            "job_preference_employment_types",
            "job_preference_industries",
            "job_preference_work_models",
            "deal_breakers",
        ),
    ),
    DataCategory(
        "resumes",
        "Resumes",
        "Your uploaded resume files, the facts extracted from them and your review of those "
        "facts; the source of truth for every generated document.",
        _UNTIL_DELETED,
        ("resumes", "resume_profiles", "candidate_profile_versions"),
    ),
    DataCategory(
        "jobs",
        "Jobs and matches",
        "Your job searches, the jobs in your list and how well each one matches you.",
        _UNTIL_DELETED,
        ("discovery_runs", "user_jobs", "job_matches", "job_match_explanations"),
    ),
    DataCategory(
        "applications",
        "Applications",
        "Your applications: preparation data, questions and answers, generated CVs and "
        "cover letters, your CV photo and the tracking timeline.",
        _UNTIL_DELETED,
        (
            "applications",
            "application_context_snapshots",
            "application_status_history",
            "application_photos",
            "application_questions",
            "application_question_answers",
            "application_question_generations",
            "generated_documents",
        ),
    ),
    DataCategory(
        "browser_assistant",
        "Browser Assistant and submissions",
        "What the Browser Assistant saw and did on application pages, your final reviews, "
        "consents you gave on employer forms, submission authorizations and results -- the "
        "audit trail of the Human Control model.",
        _UNTIL_DELETED,
        (
            "automation_sessions",
            "automation_fields",
            "automation_events",
            "automation_final_reviews",
            "automation_consent_authorizations",
            "automation_submission_authorizations",
            "automation_submission_attempts",
        ),
    ),
    DataCategory(
        "privacy_and_security",
        "Privacy choices and sign-ins",
        "Your recorded privacy decisions, and your sign-in sessions (when each started and "
        "ended -- no IP address or device information).",
        "Privacy decisions: until you delete your account. Sign-ins: removed at your next "
        "sign-in once expired or ended.",
        ("consent_records", "auth_sessions"),
    ),
)

#: Data that belongs to no user: public job advertisements as published by employers
#: (and JobPilot's analysis of them), and shared name catalogs. A user's export lists the
#: jobs in *their* list; deleting an account never deletes shared data other users see.
SHARED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "jobs",
        "job_source_postings",
        "job_intelligence",
        "job_requirements",
        "skills",
        "languages",
        "industries",
    }
)

#: Child tables without a user_id column: (column, parent table) they belong through.
CHILD_LINKS: Final[dict[str, tuple[str, str]]] = {
    "job_preference_job_titles": ("job_preferences_id", "job_preferences"),
    "job_preference_locations": ("job_preferences_id", "job_preferences"),
    "job_preference_employment_types": ("job_preferences_id", "job_preferences"),
    "job_preference_industries": ("job_preferences_id", "job_preferences"),
    "job_preference_work_models": ("job_preferences_id", "job_preferences"),
    "application_context_snapshots": ("application_id", "applications"),
    "job_match_explanations": ("job_match_id", "job_matches"),
}

#: Never exported: credentials and internal storage locations.
EXCLUDED_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "users": frozenset({"password_hash"}),
    "resumes": frozenset({"storage_reference"}),
    "application_photos": frozenset({"storage_reference"}),
    "auth_sessions": frozenset({"id"}),
}

#: Files kept outside the database (resume uploads, CV photos).
FILE_STORAGE = (
    "Resume files and CV photos are stored as files under a folder that belongs to your "
    "account; the database holds only a reference. Both are deleted with your account."
)

#: Storage in your browser. JobPilot sets no cookies and loads no third-party scripts.
BROWSER_STORAGE: Final[tuple[dict[str, str | bool], ...]] = (
    {
        "name": "jobpilot_access_token",
        "type": "localStorage",
        "purpose": "Keeps you signed in (your access token). Removed when you sign out.",
        "strictly_necessary": True,
    },
)


def user_tables() -> list[str]:
    return [table for category in CATEGORIES for table in category.tables]


def processors() -> list[dict[str, str]]:
    """Who else receives data, as configured on this server."""
    provider = settings.AI_PROVIDER.strip().lower()
    result: list[dict[str, str]] = []
    if provider == "anthropic":
        result.append(
            {
                "name": "Anthropic (Claude API)",
                "role": "External AI provider",
                "data": (
                    "Resume text (e-mail addresses and phone numbers replaced by placeholders), "
                    "profile and resume facts, job texts, application questions and your "
                    "letter drafts -- only when you use an AI feature."
                ),
                "purpose": (
                    "Resume analysis, tailored CV / cover letter, answer suggestions, "
                    "form field mapping and match explanations."
                ),
                "control": "Settings > Privacy: switch external AI processing off at any time.",
            }
        )
    result.append(
        {
            "name": "Employer application websites",
            "role": "Recipient chosen by you",
            "data": "The answers and documents you approved, only when you submit through "
            "the Browser Assistant.",
            "purpose": "Your job application.",
            "control": "Nothing is sent without your final review and explicit authorization.",
        }
    )
    return result
