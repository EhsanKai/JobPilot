"""v0.7 application workspace

Revision ID: e8b3c1a5d7f2
Revises: c7d2e9f4b1a6
Create Date: 2026-10-06 22:00:00.000000

"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e8b3c1a5d7f2'
down_revision: Union[str, None] = 'c7d2e9f4b1a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - `applications` already exists (V0 foundation table; no code ever created a
#   row). It is evolved in place, like `jobs` was in V0.3, so nothing is
#   duplicated and any row that does exist survives:
#     start_date          -> available_from   (renamed; the same concept)
#     salary_expectation, salary_currency     (kept, reused as they are)
#     user_id, job_id, status, created_at, updated_at   (kept untouched)
#   New: user_job_id (NOT NULL, UNIQUE: an application belongs to exactly one
#   UserJob, and a UserJob has at most one application), source_posting_id,
#   resume_id, mention_salary_in_letter, notice_period_value / _unit,
#   work_authorization, application_language, notes.
# - Ownership is enforced by the database: the composite foreign key
#   (user_job_id, user_id) -> user_jobs (id, user_id) means an application can
#   only reference a UserJob of its own user, the same technique V0.5 uses for
#   job_matches. It needs a unique constraint on user_jobs (id, user_id),
#   which is trivially satisfied (id is the primary key).
# - application_status gains 'PREPARING', the only status V0.7 uses. The V0
#   values (DRAFT ... WITHDRAWN) are the V0.12 lifecycle placeholders; they are
#   kept as they are and no existing row's status is changed.
# - Existing (V0) applications are attached to the UserJob of their (user,
#   job): found, or created when the user never had one (discovered/saved
#   timestamps stay NULL: the relationship exists "for another reason"). Rows
#   that cannot be attached to exactly one UserJob -- no job, or several
#   applications of one user for the same job -- stop the migration with a
#   clear error rather than being merged or deleted. V0-V0.6 had no way to
#   create applications, so none are expected.
# - Every existing application also gets its context snapshot, rebuilt from
#   the job as it is when this migration runs (origin "migrated"): the
#   earliest state still available. The document is built here, self-contained
#   and frozen at schema application_context_v1, rather than by importing
#   application code that will evolve.
# - New types (notice_period_unit, work_authorization) are created explicitly
#   and dropped in downgrade(). 'PREPARING' can't be removed from a Postgres
#   enum, so downgrade() turns PREPARING rows into DRAFT (the closest V0 value)
#   and recreates application_status without it.
# - downgrade() drops everything V0.7 added -- V0.7-only data (notes, language,
#   notice period, snapshots...) is lost, every V0-V0.6 column and row is kept.
#   user_jobs rows created for V0 applications stay (they are valid rows).

APPLICATION_STATUS_V06 = (
    'DRAFT', 'IN_PROGRESS', 'SUBMITTED', 'INTERVIEWING', 'OFFER', 'REJECTED', 'WITHDRAWN',
)
NOTICE_PERIOD_UNIT_VALUES = ('DAYS', 'WEEKS', 'MONTHS')
WORK_AUTHORIZATION_VALUES = ('AUTHORIZED', 'REQUIRES_SPONSORSHIP', 'UNKNOWN')

CONTEXT_SCHEMA_VERSION = 'application_context_v1'
# Frozen copy of V0.4's SOURCES_WITH_HOSTED_APPLICATION (sources whose posting
# page itself hosts the application form).
_HOSTED_APPLICATION_SOURCES = ('greenhouse', 'lever')


def _check_legacy_applications(bind: sa.engine.Connection) -> None:
    """Stop, before anything changes, on V0 rows that cannot become an
    application of exactly one UserJob."""
    orphaned = bind.execute(
        sa.text('SELECT count(*) FROM applications WHERE job_id IS NULL')
    ).scalar_one()
    if orphaned:
        raise RuntimeError(
            f'V0.7 requires every application to belong to a job, but {orphaned} existing '
            'row(s) in "applications" have no job_id (their job was deleted). Delete those '
            'rows or set applications.job_id, then run the migration again.'
        )
    duplicated = bind.execute(
        sa.text(
            'SELECT count(*) FROM '
            '(SELECT 1 FROM applications GROUP BY user_id, job_id HAVING count(*) > 1) AS d'
        )
    ).scalar_one()
    if duplicated:
        raise RuntimeError(
            f'V0.7 allows one application per user and job, but {duplicated} user/job '
            'combination(s) in "applications" have several. Delete the extra rows, then run '
            'the migration again.'
        )
    negative = bind.execute(
        sa.text('SELECT count(*) FROM applications WHERE salary_expectation < 0')
    ).scalar_one()
    if negative:
        raise RuntimeError(
            f'V0.7 requires a non-negative salary expectation, but {negative} existing row(s) '
            'in "applications" have a negative one. Correct them, then run the migration again.'
        )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _backfill_context_snapshots(bind: sa.engine.Connection) -> None:
    """One context snapshot per existing application, from the job as it is now."""
    rows = bind.execute(
        sa.text(
            """
            SELECT a.id AS application_id,
                   j.id AS job_id, j.canonical_url, j.apply_url, j.title, j.company_name,
                   j.location, j.locations, j.country, j.department, j.description,
                   j.requirements, j.responsibilities, j.technologies, j.salary_min,
                   j.salary_max, j.salary_currency, j.salary_period::text AS salary_period,
                   j.salary_text, j.employment_type::text AS employment_type,
                   j.work_model::text AS work_model, j.seniority::text AS seniority,
                   j.industry, j.posted_at,
                   p.id AS posting_id, p.source, p.source_job_id, p.source_company_key,
                   p.source_url, p.canonical_source_url, p.apply_url AS posting_apply_url,
                   p.title AS posting_title, p.company_name AS posting_company_name,
                   p.location AS posting_location, p.posted_at AS posting_posted_at,
                   p.source_updated_at AS posting_source_updated_at
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN job_source_postings p ON p.id = j.preferred_source_posting_id
            """
        )
    ).mappings().all()
    captured_at = _iso(datetime.now(timezone.utc))

    def enum_value(label: str | None) -> str | None:
        # Postgres labels are the upper-cased values (EMPLOYMENT_TYPE 'FULL_TIME' -> 'full_time').
        return label.lower() if label else None

    def money(value: object) -> str | None:
        return None if value is None else f'{value:.2f}'

    for row in rows:
        job = {
            'job_id': str(row['job_id']),
            'canonical_url': row['canonical_url'],
            'apply_url': row['apply_url'],
            'title': row['title'],
            'company_name': row['company_name'],
            'location': row['location'],
            'locations': list(row['locations'] or []),
            'country': row['country'],
            'department': row['department'],
            'description': row['description'],
            'requirements': list(row['requirements'] or []),
            'responsibilities': list(row['responsibilities'] or []),
            'technologies': list(row['technologies'] or []),
            'salary_min': money(row['salary_min']),
            'salary_max': money(row['salary_max']),
            'salary_currency': row['salary_currency'],
            'salary_period': enum_value(row['salary_period']),
            'salary_text': row['salary_text'],
            'employment_type': enum_value(row['employment_type']),
            'work_model': enum_value(row['work_model']),
            'seniority': enum_value(row['seniority']),
            'industry': row['industry'],
            'posted_at': _iso(row['posted_at']),
        }
        if row['posting_id'] is not None:
            source = {
                'posting_id': str(row['posting_id']),
                'source': row['source'],
                'source_job_id': row['source_job_id'],
                'source_company_key': row['source_company_key'],
                'title': row['posting_title'],
                'company_name': row['posting_company_name'],
                'location': row['posting_location'],
                'posted_at': _iso(row['posting_posted_at']),
                'source_updated_at': _iso(row['posting_source_updated_at']),
                'original_url': row['source_url'] or row['canonical_source_url'],
                'canonical_url': row['canonical_source_url'],
                'apply_url': row['posting_apply_url'],
                'application_url': row['posting_apply_url']
                or (row['canonical_source_url'] if row['source'] in _HOSTED_APPLICATION_SOURCES else None),
                'was_preferred': True,
            }
        else:
            source = {
                'posting_id': None,
                'source': 'unknown',
                'source_job_id': None,
                'source_company_key': None,
                'title': row['title'],
                'company_name': row['company_name'],
                'location': row['location'],
                'posted_at': None,
                'source_updated_at': None,
                'original_url': row['canonical_url'],
                'canonical_url': row['canonical_url'],
                'apply_url': row['apply_url'],
                'application_url': row['apply_url'],
                'was_preferred': False,
            }
        document = {
            'schema_version': CONTEXT_SCHEMA_VERSION,
            'origin': 'migrated',
            'captured_at': captured_at,
            'job': job,
            'source': source,
        }
        encoded = json.dumps(
            document, sort_keys=True, separators=(',', ':'), ensure_ascii=False
        ).encode()
        bind.execute(
            sa.text(
                'INSERT INTO application_context_snapshots '
                '(id, application_id, schema_version, content_hash, document) '
                'VALUES (:id, :application_id, :schema_version, :content_hash, '
                'CAST(:document AS jsonb))'
            ),
            {
                'id': uuid.uuid4(),
                'application_id': row['application_id'],
                'schema_version': CONTEXT_SCHEMA_VERSION,
                'content_hash': hashlib.sha256(encoded).hexdigest(),
                'document': json.dumps(document, ensure_ascii=False),
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    _check_legacy_applications(bind)

    op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'PREPARING'")
    sa.Enum(*NOTICE_PERIOD_UNIT_VALUES, name='notice_period_unit').create(bind, checkfirst=True)
    sa.Enum(*WORK_AUTHORIZATION_VALUES, name='work_authorization').create(bind, checkfirst=True)

    # --- applications: evolve the V0 table ------------------------------------------
    op.alter_column('applications', 'start_date', new_column_name='available_from')
    op.add_column('applications', sa.Column('user_job_id', sa.UUID(), nullable=True))
    op.add_column('applications', sa.Column('source_posting_id', sa.UUID(), nullable=True))
    op.add_column('applications', sa.Column('resume_id', sa.UUID(), nullable=True))
    op.add_column('applications', sa.Column('mention_salary_in_letter', sa.Boolean(), nullable=True))
    op.add_column('applications', sa.Column('notice_period_value', sa.Integer(), nullable=True))
    op.add_column('applications', sa.Column(
        'notice_period_unit',
        postgresql.ENUM(*NOTICE_PERIOD_UNIT_VALUES, name='notice_period_unit', create_type=False),
        nullable=True,
    ))
    op.add_column('applications', sa.Column(
        'work_authorization',
        postgresql.ENUM(*WORK_AUTHORIZATION_VALUES, name='work_authorization', create_type=False),
        nullable=True,
    ))
    op.add_column('applications', sa.Column('application_language', sa.String(length=2), nullable=True))
    op.add_column('applications', sa.Column('notes', sa.Text(), nullable=True))

    # --- Attach V0 applications to their UserJob ----------------------------------------
    op.create_unique_constraint('uq_user_jobs_id_user', 'user_jobs', ['id', 'user_id'])
    op.execute(
        """
        INSERT INTO user_jobs (id, user_id, job_id, first_discovered_at, last_discovered_at)
        SELECT gen_random_uuid(), pairs.user_id, pairs.job_id, NULL, NULL
        FROM (SELECT DISTINCT user_id, job_id FROM applications) AS pairs
        WHERE NOT EXISTS (
            SELECT 1 FROM user_jobs uj
            WHERE uj.user_id = pairs.user_id AND uj.job_id = pairs.job_id
        )
        """
    )
    op.execute(
        """
        UPDATE applications AS a
        SET user_job_id = uj.id
        FROM user_jobs AS uj
        WHERE uj.user_id = a.user_id AND uj.job_id = a.job_id
        """
    )
    op.alter_column('applications', 'user_job_id', nullable=False)

    # --- Constraints and indexes ----------------------------------------------------------
    op.create_foreign_key(
        'fk_applications_user_job', 'applications', 'user_jobs',
        ['user_job_id', 'user_id'], ['id', 'user_id'], ondelete='CASCADE',
    )
    op.create_unique_constraint('uq_applications_user_job_id', 'applications', ['user_job_id'])
    op.create_foreign_key(
        'applications_source_posting_id_fkey', 'applications', 'job_source_postings',
        ['source_posting_id'], ['id'], ondelete='SET NULL',
    )
    op.create_foreign_key(
        'applications_resume_id_fkey', 'applications', 'resumes',
        ['resume_id'], ['id'], ondelete='SET NULL',
    )
    op.create_check_constraint(
        'ck_applications_salary_expectation', 'applications',
        'salary_expectation IS NULL OR salary_expectation >= 0',
    )
    op.create_check_constraint(
        'ck_applications_notice_period_pair', 'applications',
        '(notice_period_value IS NULL) = (notice_period_unit IS NULL)',
    )
    op.create_check_constraint(
        'ck_applications_notice_period_value', 'applications',
        'notice_period_value IS NULL OR notice_period_value >= 0',
    )
    op.create_check_constraint(
        'ck_applications_application_language', 'applications',
        "application_language IS NULL OR application_language ~ '^[a-z]{2}$'",
    )
    op.create_index(op.f('ix_applications_source_posting_id'), 'applications', ['source_posting_id'], unique=False)
    op.create_index(op.f('ix_applications_resume_id'), 'applications', ['resume_id'], unique=False)
    op.create_index('ix_applications_user_id_updated_at', 'applications', ['user_id', 'updated_at'], unique=False)

    # --- application_context_snapshots ----------------------------------------------------
    op.create_table('application_context_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('schema_version', sa.String(length=50), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', name='uq_application_context_snapshots_application_id'),
    )
    _backfill_context_snapshots(bind)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table('application_context_snapshots')

    op.drop_index('ix_applications_user_id_updated_at', table_name='applications')
    op.drop_index(op.f('ix_applications_resume_id'), table_name='applications')
    op.drop_index(op.f('ix_applications_source_posting_id'), table_name='applications')
    for check in (
        'ck_applications_application_language', 'ck_applications_notice_period_value',
        'ck_applications_notice_period_pair', 'ck_applications_salary_expectation',
    ):
        op.drop_constraint(check, 'applications', type_='check')
    op.drop_constraint('applications_resume_id_fkey', 'applications', type_='foreignkey')
    op.drop_constraint('applications_source_posting_id_fkey', 'applications', type_='foreignkey')
    op.drop_constraint('uq_applications_user_job_id', 'applications', type_='unique')
    op.drop_constraint('fk_applications_user_job', 'applications', type_='foreignkey')
    for column in (
        'notes', 'application_language', 'work_authorization', 'notice_period_unit',
        'notice_period_value', 'mention_salary_in_letter', 'resume_id', 'source_posting_id',
        'user_job_id',
    ):
        op.drop_column('applications', column)
    op.drop_constraint('uq_user_jobs_id_user', 'user_jobs', type_='unique')
    op.alter_column('applications', 'available_from', new_column_name='start_date')

    sa.Enum(name='work_authorization').drop(bind, checkfirst=True)
    sa.Enum(name='notice_period_unit').drop(bind, checkfirst=True)

    # 'PREPARING' can't exist in V0.6's type: those rows become DRAFT.
    op.execute("UPDATE applications SET status = 'DRAFT' WHERE status = 'PREPARING'")
    op.execute("ALTER TYPE application_status RENAME TO application_status_v07")
    values = ", ".join(f"'{value}'" for value in APPLICATION_STATUS_V06)
    op.execute(f"CREATE TYPE application_status AS ENUM ({values})")
    op.execute(
        "ALTER TABLE applications ALTER COLUMN status TYPE application_status "
        "USING status::text::application_status"
    )
    op.execute("DROP TYPE application_status_v07")
