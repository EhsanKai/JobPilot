"""The user's application for a job, for embedding in job list and details
(V0.7). Kept apart from `service` because `job_service` uses it."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.user import User
from app.models.user_job import UserJob
from app.schemas.application import ApplicationRefRead


def application_refs(
    db: Session, user: User, job_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ApplicationRefRead]:
    """The user's own application per job (only ever the user's own rows),
    reached through their UserJob."""
    ids = list(job_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(UserJob.job_id, Application.id, Application.status)
        .join(Application, Application.user_job_id == UserJob.id)
        .where(UserJob.user_id == user.id, UserJob.job_id.in_(ids))
    ).all()
    return {
        job_id: ApplicationRefRead(id=application_id, status=status)
        for job_id, application_id, status in rows
    }
