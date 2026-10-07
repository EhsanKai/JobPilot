"""v0.12 application tracking

Revision ID: d6a3f1b8c2e5
Revises: c5f2a8e1d9b3
Create Date: 2026-10-10 09:00:00.000000

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd6a3f1b8c2e5'
down_revision: Union[str, None] = 'c5f2a8e1d9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - applications.status moves to the V0.12 tracking lifecycle. Postgres cannot drop or
#   rename enum values, so the type is replaced: the old one is renamed aside, the new
#   one created, the column converted with an explicit mapping, and the old type dropped.
#     DRAFT        -> SAVED
#     IN_PROGRESS  -> PREPARING
#     PREPARING    -> PREPARING
#     SUBMITTED    -> APPLIED        (V0.11 set it for a confirmed or user-verified
#                                     submission; the technical record stays in
#                                     automation_submission_attempts, untouched)
#     INTERVIEWING -> INTERVIEW
#     OFFER, REJECTED, WITHDRAWN -> unchanged
#   Nothing in V0-V0.11 could produce anything but PREPARING and SUBMITTED; the rest
#   are mapped so that no possible row is lost or left unmapped.
# - New columns: applied_at, last_status_change_at (NOT NULL), follow_up_at.
#   Existing rows: last_status_change_at = updated_at; applied_at (only for rows that
#   are applied now) = the submission's confirmation time, else the user's verification
#   time, else updated_at.
# - application_status_history is created, and every existing application gets the
#   history it would have had: SAVED (when the job came into the user's list, never
#   later than the application) -> PREPARING (application created) -> its current status
#   (when it changed), all by SYSTEM; the last one by SUBMISSION when it is APPLIED.
#   So `applications.status` equals the latest history row for every existing row.
# - downgrade() drops the history, the new columns, constraints and index, and converts
#   the column back. Tracking-only statuses have no V0.11 equivalent and become the nearest
#   one: SAVED -> DRAFT, FOLLOW_UP -> SUBMITTED, NO_RESPONSE -> WITHDRAWN (a closed
#   application; REJECTED would claim something the employer never said). APPLIED ->
#   SUBMITTED. The history and follow-up dates are lost; every application survives.

STATUS_V012 = (
    'SAVED', 'PREPARING', 'APPLIED', 'FOLLOW_UP', 'INTERVIEW', 'OFFER', 'REJECTED',
    'WITHDRAWN', 'NO_RESPONSE',
)
STATUS_V011 = (
    'DRAFT', 'IN_PROGRESS', 'SUBMITTED', 'INTERVIEWING', 'OFFER', 'REJECTED', 'WITHDRAWN',
    'PREPARING',
)
ENUMS = {
    'status_history_entry_type': ('STATUS_CHANGE', 'CORRECTION', 'NOTE'),
    'status_change_source': ('SYSTEM', 'USER', 'SUBMISSION'),
    'status_change_actor': ('USER', 'SYSTEM'),
}

UPGRADE_MAPPING = """
    CASE status::text
        WHEN 'DRAFT' THEN 'SAVED'
        WHEN 'IN_PROGRESS' THEN 'PREPARING'
        WHEN 'SUBMITTED' THEN 'APPLIED'
        WHEN 'INTERVIEWING' THEN 'INTERVIEW'
        ELSE status::text
    END
"""
DOWNGRADE_MAPPING = """
    CASE status::text
        WHEN 'SAVED' THEN 'DRAFT'
        WHEN 'APPLIED' THEN 'SUBMITTED'
        WHEN 'FOLLOW_UP' THEN 'SUBMITTED'
        WHEN 'INTERVIEW' THEN 'INTERVIEWING'
        WHEN 'NO_RESPONSE' THEN 'WITHDRAWN'
        ELSE status::text
    END
"""


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _replace_status_type(values: tuple[str, ...], mapping: str, aside: str) -> None:
    op.execute(f"ALTER TYPE application_status RENAME TO {aside}")
    listed = ", ".join(f"'{value}'" for value in values)
    op.execute(f"CREATE TYPE application_status AS ENUM ({listed})")
    op.execute(
        "ALTER TABLE applications ALTER COLUMN status TYPE application_status "
        f"USING ({mapping})::application_status"
    )
    op.execute(f"DROP TYPE {aside}")


def _backfill_history(bind: sa.engine.Connection) -> None:
    """The history every existing application would have had (see the notes above)."""
    rows = bind.execute(
        sa.text(
            """
            SELECT a.id, a.user_id, a.status::text AS status, a.created_at, a.updated_at,
                   uj.saved_at, uj.first_discovered_at,
                   (SELECT t.id FROM automation_submission_attempts t
                     WHERE t.application_id = a.id AND t.result = 'SUCCESS'
                     ORDER BY t.finished_at DESC LIMIT 1) AS success_id,
                   (SELECT t.finished_at FROM automation_submission_attempts t
                     WHERE t.application_id = a.id AND t.result = 'SUCCESS'
                     ORDER BY t.finished_at DESC LIMIT 1) AS success_at,
                   (SELECT t.id FROM automation_submission_attempts t
                     WHERE t.application_id = a.id AND t.user_verified_outcome = 'SUBMITTED'
                     ORDER BY t.user_verified_at DESC LIMIT 1) AS verified_id,
                   (SELECT t.user_verified_at FROM automation_submission_attempts t
                     WHERE t.application_id = a.id AND t.user_verified_outcome = 'SUBMITTED'
                     ORDER BY t.user_verified_at DESC LIMIT 1) AS verified_at
              FROM applications a
              JOIN user_jobs uj ON uj.id = a.user_job_id
            """
        )
    ).mappings().all()

    insert = sa.text(
        """
        INSERT INTO application_status_history
            (id, user_id, application_id, sequence, entry_type, previous_status, new_status,
             changed_at, source, actor, note, submission_attempt_id)
        VALUES
            (:id, :user_id, :application_id, :sequence, 'STATUS_CHANGE', :previous, :new,
             :changed_at, :source, :actor, NULL, :attempt_id)
        """
    )
    for row in rows:
        saved_at = row['saved_at'] or row['first_discovered_at'] or row['created_at']
        saved_at = min(saved_at, row['created_at'])
        events: list[tuple[str | None, str, datetime, str, str, object]] = [
            (None, 'SAVED', saved_at, 'SYSTEM', 'SYSTEM', None),
        ]
        status = row['status']
        applied_at = None
        if status == 'SAVED':
            changed_at = row['updated_at']
        else:
            events.append(('SAVED', 'PREPARING', row['created_at'], 'SYSTEM', 'SYSTEM', None))
            changed_at = row['updated_at']
        if status not in ('SAVED', 'PREPARING'):
            source, actor, attempt_id = 'SYSTEM', 'SYSTEM', None
            if status == 'APPLIED':
                source = 'SUBMISSION'
                if row['success_at'] is not None:
                    changed_at, attempt_id = row['success_at'], row['success_id']
                elif row['verified_at'] is not None:
                    changed_at, attempt_id = row['verified_at'], row['verified_id']
                    actor = 'USER'
            events.append(('PREPARING', status, max(changed_at, row['created_at']), source, actor, attempt_id))
            if status in ('APPLIED', 'FOLLOW_UP', 'INTERVIEW', 'OFFER'):
                applied_at = events[-1][2]
        last_change = events[-1][2] if len(events) > 1 else row['created_at']
        for sequence, (previous, new, at, source, actor, attempt_id) in enumerate(events, start=1):
            bind.execute(
                insert,
                {
                    'id': uuid.uuid4(), 'user_id': row['user_id'], 'application_id': row['id'],
                    'sequence': sequence, 'previous': previous, 'new': new, 'changed_at': at,
                    'source': source, 'actor': actor, 'attempt_id': attempt_id,
                },
            )
        bind.execute(
            sa.text(
                "UPDATE applications SET applied_at = :applied_at, "
                "last_status_change_at = :last_change WHERE id = :id"
            ),
            {'applied_at': applied_at, 'last_change': last_change, 'id': row['id']},
        )


def upgrade() -> None:
    bind = op.get_bind()

    _replace_status_type(STATUS_V012, UPGRADE_MAPPING, 'application_status_v011')

    op.add_column('applications', sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('applications', sa.Column('last_status_change_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('applications', sa.Column('follow_up_at', sa.DateTime(timezone=True), nullable=True))

    for name, values in ENUMS.items():
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    op.create_table('application_status_history',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('entry_type', _enum('status_history_entry_type'), nullable=False),
    sa.Column('previous_status', postgresql.ENUM(*STATUS_V012, name='application_status', create_type=False), nullable=True),
    sa.Column('new_status', postgresql.ENUM(*STATUS_V012, name='application_status', create_type=False), nullable=False),
    sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source', _enum('status_change_source'), nullable=False),
    sa.Column('actor', _enum('status_change_actor'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('submission_attempt_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("(entry_type = 'NOTE') = (previous_status IS NOT DISTINCT FROM new_status AND previous_status IS NOT NULL)", name='ck_application_status_history_note_keeps_status'),
    sa.CheckConstraint("entry_type <> 'CORRECTION' OR (note IS NOT NULL AND source = 'USER')", name='ck_application_status_history_correction'),
    sa.CheckConstraint("entry_type <> 'NOTE' OR note IS NOT NULL", name='ck_application_status_history_note'),
    sa.CheckConstraint('sequence >= 1', name='ck_application_status_history_sequence'),
    sa.CheckConstraint("source <> 'USER' OR actor = 'USER'", name='ck_application_status_history_user_actor'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_application_status_history_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['submission_attempt_id'], ['automation_submission_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', 'sequence', name='uq_application_status_history_sequence')
    )
    op.create_index('ix_application_status_history_user_id', 'application_status_history', ['user_id'], unique=False)

    # Existing rows first, then the constraints that describe them.
    op.execute("UPDATE applications SET last_status_change_at = updated_at")
    _backfill_history(bind)

    op.create_check_constraint(
        'ck_applications_applied_at', 'applications',
        "status NOT IN ('APPLIED', 'FOLLOW_UP', 'INTERVIEW', 'OFFER') OR applied_at IS NOT NULL",
    )
    op.create_check_constraint(
        'ck_applications_follow_up_at', 'applications',
        "follow_up_at IS NULL OR status IN ('APPLIED', 'FOLLOW_UP', 'INTERVIEW')",
    )
    op.create_index('ix_applications_user_id_status', 'applications', ['user_id', 'status'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index('ix_applications_user_id_status', table_name='applications')
    op.drop_constraint('ck_applications_follow_up_at', 'applications', type_='check')
    op.drop_constraint('ck_applications_applied_at', 'applications', type_='check')

    op.drop_index('ix_application_status_history_user_id', table_name='application_status_history')
    op.drop_table('application_status_history')
    for name in ENUMS:
        sa.Enum(name=name).drop(bind, checkfirst=True)

    op.drop_column('applications', 'follow_up_at')
    op.drop_column('applications', 'last_status_change_at')
    op.drop_column('applications', 'applied_at')

    _replace_status_type(STATUS_V011, DOWNGRADE_MAPPING, 'application_status_v012')
