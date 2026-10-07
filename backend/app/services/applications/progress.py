"""The preparation checklist of an application (V0.7).

Deterministic and stateless: each item is complete exactly when its data is
present on the application. Nothing is stored (so it can never disagree with
the data) and there is no workflow engine -- later versions add their own
steps (documents generated, reviewed...) when they exist.

    job             always: the application exists because a job was chosen
    resume          a resume version is referenced
    language        the user chose the application language
    salary          a salary expectation with its currency
    availability    the date the candidate can start
    notice_period   the candidate's notice period
    work_authorization   optional: not every application asks, so it is shown
                         but never counted
"""

from app.models.application import Application
from app.schemas.application import PreparationItemRead, PreparationKey, PreparationProgressRead

#: (key, required) in display order.
PREPARATION_ITEMS: tuple[tuple[PreparationKey, bool], ...] = (
    ("job", True),
    ("resume", True),
    ("language", True),
    ("salary", True),
    ("availability", True),
    ("notice_period", True),
    ("work_authorization", False),
)


def _is_complete(key: PreparationKey, application: Application) -> bool:
    match key:
        case "job":
            return True
        case "resume":
            return application.resume_id is not None
        case "language":
            return application.application_language is not None
        case "salary":
            return (
                application.salary_expectation is not None
                and application.salary_currency is not None
            )
        case "availability":
            return application.available_from is not None
        case "notice_period":
            return application.notice_period_value is not None
        case "work_authorization":
            return application.work_authorization is not None


def preparation_progress(application: Application) -> PreparationProgressRead:
    items = [
        PreparationItemRead(key=key, complete=_is_complete(key, application), required=required)
        for key, required in PREPARATION_ITEMS
    ]
    required_items = [item for item in items if item.required]
    return PreparationProgressRead(
        completed=sum(item.complete for item in required_items),
        total=len(required_items),
        items=items,
    )
