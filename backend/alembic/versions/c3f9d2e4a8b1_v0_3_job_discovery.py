"""v0.3 job discovery

Revision ID: c3f9d2e4a8b1
Revises: a7c2e5d91f30
Create Date: 2026-10-05 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3f9d2e4a8b1'
down_revision: Union[str, None] = 'a7c2e5d91f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - `jobs` already exists (V0 foundation table, never populated by V0-V0.2).
#   It is evolved in place rather than recreated, so `applications.job_id`
#   keeps pointing at it and any manually inserted rows survive:
#   external_id -> source_job_id, company -> company_name, url -> source_url.
# - Existing rows get `canonical_url = source_url` and a `dedup_key`: the
#   id-based key when (source, source_job_id) is unambiguous, otherwise
#   'legacy:<id>', which can never collide with keys JobPilot generates.
#   A legacy row without a URL can't satisfy "every job links to its
#   original posting", so the migration stops with a clear error instead of
#   inventing a URL.
# - The enum types employment_type, remote_preference and
#   professional_seniority_level are reused (create_type=False) so jobs and
#   preferences share one vocabulary. They are never dropped here.
# - employment_type gains 'WORKING_STUDENT'. The ORM has had this value since
#   V0.1, but no migration added it, so Alembic-built databases rejected it.
#   Postgres can't remove an enum value, so downgrade() leaves it in place
#   (it is harmless to older code).
# - New types (salary_period, discovery_run_status) are created explicitly
#   and dropped explicitly in downgrade(), as in previous migrations.

SALARY_PERIOD_VALUES = ('YEAR', 'MONTH', 'WEEK', 'DAY', 'HOUR')
DISCOVERY_RUN_STATUS_VALUES = ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')
EMPLOYMENT_TYPE_VALUES = (
    'FULL_TIME', 'PART_TIME', 'CONTRACT', 'INTERNSHIP', 'TEMPORARY', 'FREELANCE', 'WORKING_STUDENT',
)
REMOTE_PREFERENCE_VALUES = ('ONSITE', 'REMOTE', 'HYBRID', 'FLEXIBLE')
SENIORITY_VALUES = ('INTERN', 'JUNIOR', 'MID', 'SENIOR', 'LEAD', 'MANAGER', 'DIRECTOR')

_JSONB_LIST_COLUMNS = ('locations', 'requirements', 'responsibilities', 'technologies')


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TYPE employment_type ADD VALUE IF NOT EXISTS 'WORKING_STUDENT'")
    sa.Enum(*SALARY_PERIOD_VALUES, name='salary_period').create(bind, checkfirst=True)
    sa.Enum(*DISCOVERY_RUN_STATUS_VALUES, name='discovery_run_status').create(
        bind, checkfirst=True
    )

    # --- jobs: evolve the V0 table into the canonical Job ------------------
    op.alter_column('jobs', 'external_id', new_column_name='source_job_id')
    op.alter_column('jobs', 'company', new_column_name='company_name')
    op.alter_column('jobs', 'url', new_column_name='source_url')
    op.alter_column('jobs', 'title', type_=sa.String(length=500))
    op.alter_column('jobs', 'location', type_=sa.String(length=500))
    op.alter_column('jobs', 'source_url', type_=sa.String(length=2000))
    op.alter_column('jobs', 'description', type_=sa.Text())
    op.alter_column(
        'jobs', 'raw_data', type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using='raw_data::jsonb',
    )

    op.add_column('jobs', sa.Column('dedup_key', sa.String(length=400), nullable=True))
    op.add_column('jobs', sa.Column('source_company_key', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('canonical_url', sa.String(length=2000), nullable=True))
    op.add_column('jobs', sa.Column('apply_url', sa.String(length=2000), nullable=True))
    for column in _JSONB_LIST_COLUMNS:
        op.add_column(
            'jobs',
            sa.Column(
                column, postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
    op.add_column('jobs', sa.Column('country', sa.String(length=2), nullable=True))
    op.add_column('jobs', sa.Column('department', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('salary_min', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('jobs', sa.Column('salary_max', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('jobs', sa.Column('salary_currency', sa.String(length=3), nullable=True))
    op.add_column('jobs', sa.Column(
        'salary_period',
        postgresql.ENUM(*SALARY_PERIOD_VALUES, name='salary_period', create_type=False),
        nullable=True,
    ))
    op.add_column('jobs', sa.Column(
        'employment_type',
        postgresql.ENUM(*EMPLOYMENT_TYPE_VALUES, name='employment_type', create_type=False),
        nullable=True,
    ))
    op.add_column('jobs', sa.Column(
        'work_model',
        postgresql.ENUM(*REMOTE_PREFERENCE_VALUES, name='remote_preference', create_type=False),
        nullable=True,
    ))
    op.add_column('jobs', sa.Column(
        'seniority',
        postgresql.ENUM(*SENIORITY_VALUES, name='professional_seniority_level', create_type=False),
        nullable=True,
    ))
    op.add_column('jobs', sa.Column('industry', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column(
        'discovered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False,
    ))
    op.add_column('jobs', sa.Column(
        'last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False,
    ))
    op.add_column('jobs', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.add_column('jobs', sa.Column('normalizer_version', sa.String(length=50), nullable=True))

    # Backfill existing rows (V0 never wrote any, but don't assume). A row
    # whose (source, source_job_id) is unique gets the same key discovery
    # would generate, so jobs survive a downgrade/upgrade without being
    # duplicated by the next discovery run; anything ambiguous stays
    # 'legacy:<id>'.
    op.execute(
        """
        UPDATE jobs AS j
        SET canonical_url = j.source_url,
            dedup_key = CASE
                WHEN j.source_job_id IS NOT NULL AND counted.n = 1
                    THEN j.source || ':id:' || j.source_job_id
                ELSE 'legacy:' || j.id::text
            END
        FROM (
            SELECT id, COUNT(*) OVER (PARTITION BY source, source_job_id) AS n FROM jobs
        ) AS counted
        WHERE j.id = counted.id
        """
    )
    missing_url = bind.execute(sa.text('SELECT count(*) FROM jobs WHERE canonical_url IS NULL'))
    if missing_url.scalar_one():
        raise RuntimeError(
            'V0.3 requires every job to link to its original posting, but some existing '
            'rows in "jobs" have no url. Set jobs.url for those rows (or delete them) and '
            'run the migration again.'
        )
    op.alter_column('jobs', 'canonical_url', nullable=False)
    op.alter_column('jobs', 'dedup_key', nullable=False)
    # The ORM always supplies list values; the server defaults only existed to
    # backfill legacy rows.
    for column in _JSONB_LIST_COLUMNS:
        op.alter_column('jobs', column, server_default=None)

    op.create_unique_constraint('uq_jobs_dedup_key', 'jobs', ['dedup_key'])
    op.create_index('ix_jobs_source_source_job_id', 'jobs', ['source', 'source_job_id'])
    op.create_index('ix_jobs_posted_at', 'jobs', ['posted_at'])

    # --- user_jobs ---------------------------------------------------------------
    op.create_table('user_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('first_discovered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('last_discovered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('saved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'job_id', name='uq_user_jobs_user_job')
    )
    op.create_index(op.f('ix_user_jobs_job_id'), 'user_jobs', ['job_id'], unique=False)
    op.create_index(op.f('ix_user_jobs_user_id'), 'user_jobs', ['user_id'], unique=False)

    # --- discovery_runs ------------------------------------------------------------
    op.create_table('discovery_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('status', postgresql.ENUM(*DISCOVERY_RUN_STATUS_VALUES, name='discovery_run_status', create_type=False), nullable=False),
    sa.Column('criteria', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_discovery_runs_user_id'), 'discovery_runs', ['user_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(op.f('ix_discovery_runs_user_id'), table_name='discovery_runs')
    op.drop_table('discovery_runs')
    op.drop_index(op.f('ix_user_jobs_user_id'), table_name='user_jobs')
    op.drop_index(op.f('ix_user_jobs_job_id'), table_name='user_jobs')
    op.drop_table('user_jobs')

    op.drop_index('ix_jobs_posted_at', table_name='jobs')
    op.drop_index('ix_jobs_source_source_job_id', table_name='jobs')
    op.drop_constraint('uq_jobs_dedup_key', 'jobs', type_='unique')
    for column in (
        'normalizer_version', 'content_hash', 'last_seen_at', 'discovered_at',
        'source_updated_at', 'posted_at', 'industry', 'seniority', 'work_model',
        'employment_type', 'salary_period', 'salary_currency', 'salary_max', 'salary_min',
        'department', 'country', *_JSONB_LIST_COLUMNS, 'apply_url', 'canonical_url',
        'source_company_key', 'dedup_key',
    ):
        op.drop_column('jobs', column)

    # Restore V0 types. Values longer than the V0 limits are truncated rather
    # than making the downgrade fail.
    op.alter_column(
        'jobs', 'raw_data', type_=sa.JSON(), postgresql_using='raw_data::json',
    )
    op.alter_column('jobs', 'description', type_=sa.String())
    op.alter_column(
        'jobs', 'source_url', type_=sa.String(length=1000),
        postgresql_using='left(source_url, 1000)',
    )
    op.alter_column(
        'jobs', 'location', type_=sa.String(length=255), postgresql_using='left(location, 255)',
    )
    op.alter_column(
        'jobs', 'title', type_=sa.String(length=255), postgresql_using='left(title, 255)',
    )
    op.alter_column('jobs', 'source_url', new_column_name='url')
    op.alter_column('jobs', 'company_name', new_column_name='company')
    op.alter_column('jobs', 'source_job_id', new_column_name='external_id')

    sa.Enum(name='discovery_run_status').drop(bind, checkfirst=True)
    sa.Enum(name='salary_period').drop(bind, checkfirst=True)
