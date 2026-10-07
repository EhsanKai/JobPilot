"""v0.4 job intelligence: source postings, canonical keys

Revision ID: d4a7e3b9c2f1
Revises: c3f9d2e4a8b1
Create Date: 2026-10-06 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4a7e3b9c2f1'
down_revision: Union[str, None] = 'c3f9d2e4a8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# upgrade()
# - Source identity and provenance move from `jobs` to the new
#   `job_source_postings` table. Every existing job keeps its row and its id
#   (user_jobs.job_id and applications.job_id stay valid) and gets exactly
#   one posting carrying its V0.3 identity: source, source_job_id, dedup_key,
#   board, URLs, raw payload, content hash, normalizer version, first/last
#   seen (= discovered_at / last_seen_at). That posting becomes the job's
#   preferred posting. Nothing is invented: values V0.3 didn't have stay NULL.
# - Comparison keys (normalized_*) and the posting's normalized snapshot are
#   left NULL. They are computed by application code (deterministic
#   normalization rules that will evolve), not frozen into a migration: the
#   next discovery run backfills keys before matching anything
#   (persistence.backfill_identities) and re-stores snapshots as sources
#   return the postings again.
# - 'legacy:<id>' dedup keys (rows whose V0.3 identity was ambiguous) are
#   kept and excluded from the (source, source_job_id) unique index.
# - No enum types are created, changed or dropped.
#
# downgrade()
# - V0.3 can represent one source per job. A job with several postings
#   (merged across sources by V0.4) keeps its preferred posting's identity
#   and id; every other posting is split back out into its own V0.3 job row
#   (content copied from its own snapshot where available, otherwise from the
#   canonical job), and users who had the merged job get a user_jobs row for
#   each split job (not saved -- saved state stays on the original job).
#   Applications keep pointing at the original job. No source posting is lost.

_NEW_JOB_COLUMNS = (
    'normalized_title', 'normalized_company_name', 'normalized_location', 'city', 'salary_text',
)
_V03_IDENTITY_COLUMNS = (
    'source', 'source_job_id', 'dedup_key', 'source_company_key', 'source_url', 'raw_data',
    'content_hash', 'normalizer_version', 'source_updated_at',
)


def upgrade() -> None:
    op.create_table('job_source_postings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('source_job_id', sa.String(length=255), nullable=True),
    sa.Column('dedup_key', sa.String(length=400), nullable=False),
    sa.Column('source_company_key', sa.String(length=255), nullable=True),
    sa.Column('source_url', sa.String(length=2000), nullable=True),
    sa.Column('canonical_source_url', sa.String(length=2000), nullable=False),
    sa.Column('apply_url', sa.String(length=2000), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('company_name', sa.String(length=255), nullable=False),
    sa.Column('location', sa.String(length=500), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('url_key', sa.String(length=2000), nullable=True),
    sa.Column('normalized_title', sa.String(length=500), nullable=True),
    sa.Column('normalized_company_name', sa.String(length=255), nullable=True),
    sa.Column('normalized_location', sa.String(length=600), nullable=True),
    sa.Column('identity_version', sa.String(length=50), nullable=True),
    sa.Column('normalized_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('raw_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('normalizer_version', sa.String(length=50), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedup_key', name='uq_job_source_postings_dedup_key')
    )
    op.create_index(op.f('ix_job_source_postings_job_id'), 'job_source_postings', ['job_id'])
    op.create_index(
        'uq_job_source_postings_source_job_id', 'job_source_postings', ['source', 'source_job_id'],
        unique=True,
        postgresql_where=sa.text("source_job_id IS NOT NULL AND dedup_key NOT LIKE 'legacy:%'"),
    )
    op.create_index(
        'ix_job_source_postings_identity', 'job_source_postings',
        ['normalized_company_name', 'normalized_title'],
    )
    op.create_index('ix_job_source_postings_url_key', 'job_source_postings', ['url_key'])
    op.create_index(
        'ix_job_source_postings_board', 'job_source_postings', ['source', 'source_company_key'],
    )

    # One posting per existing job, carrying its V0.3 identity.
    op.execute(
        """
        INSERT INTO job_source_postings (
            id, job_id, source, source_job_id, dedup_key, source_company_key,
            source_url, canonical_source_url, apply_url, title, company_name, location,
            posted_at, source_updated_at, raw_data, content_hash, normalizer_version,
            first_seen_at, last_seen_at, is_active, created_at, updated_at
        )
        SELECT
            gen_random_uuid(), j.id, j.source, j.source_job_id, j.dedup_key, j.source_company_key,
            j.source_url, j.canonical_url, j.apply_url, j.title, j.company_name, j.location,
            j.posted_at, j.source_updated_at, j.raw_data, j.content_hash, j.normalizer_version,
            j.discovered_at, j.last_seen_at, true, j.created_at, j.updated_at
        FROM jobs AS j
        """
    )

    op.add_column('jobs', sa.Column('preferred_source_posting_id', sa.UUID(), nullable=True))
    op.add_column('jobs', sa.Column('salary_text', sa.String(length=500), nullable=True))
    op.add_column('jobs', sa.Column('normalized_title', sa.String(length=500), nullable=True))
    op.add_column('jobs', sa.Column('normalized_company_name', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('normalized_location', sa.String(length=600), nullable=True))
    op.add_column('jobs', sa.Column('city', sa.String(length=255), nullable=True))
    op.create_foreign_key(
        'fk_jobs_preferred_source_posting_id', 'jobs', 'job_source_postings',
        ['preferred_source_posting_id'], ['id'], ondelete='SET NULL',
    )
    op.execute(
        """
        UPDATE jobs AS j SET preferred_source_posting_id = p.id
        FROM job_source_postings AS p WHERE p.job_id = j.id
        """
    )
    op.create_index('ix_jobs_identity', 'jobs', ['normalized_company_name', 'normalized_title'])

    # Identity and provenance now live on the postings.
    op.drop_index('ix_jobs_source_source_job_id', table_name='jobs')
    op.drop_constraint('uq_jobs_dedup_key', 'jobs', type_='unique')
    for column in _V03_IDENTITY_COLUMNS:
        op.drop_column('jobs', column)


def downgrade() -> None:
    # --- Split multi-posting jobs back into one V0.3 job per posting --------------
    # Every non-preferred posting of a job with several postings gets its own
    # job row and is moved onto it.
    op.execute(
        """
        CREATE TEMPORARY TABLE _v04_split ON COMMIT DROP AS
        SELECT p.id AS posting_id, p.job_id AS old_job_id, gen_random_uuid() AS new_job_id
        FROM job_source_postings AS p
        JOIN jobs AS j ON j.id = p.job_id
        WHERE p.id IS DISTINCT FROM COALESCE(
            j.preferred_source_posting_id,
            (SELECT p2.id FROM job_source_postings AS p2 WHERE p2.job_id = j.id
             ORDER BY p2.first_seen_at, p2.id LIMIT 1)
        )
        """
    )
    op.execute(
        """
        INSERT INTO jobs (
            id, canonical_url, apply_url, title, company_name, location, locations, country,
            department, description, requirements, responsibilities, technologies,
            salary_min, salary_max, salary_currency, salary_period, salary_text,
            employment_type, work_model, seniority, industry, posted_at,
            discovered_at, last_seen_at, created_at, updated_at
        )
        SELECT
            s.new_job_id, p.canonical_source_url, p.apply_url, p.title, p.company_name,
            p.location,
            COALESCE(p.normalized_data -> 'locations', j.locations),
            j.country, j.department,
            COALESCE(p.normalized_data ->> 'description', j.description),
            COALESCE(p.normalized_data -> 'requirements', j.requirements),
            COALESCE(p.normalized_data -> 'responsibilities', j.responsibilities),
            COALESCE(p.normalized_data -> 'technologies', j.technologies),
            j.salary_min, j.salary_max, j.salary_currency, j.salary_period, j.salary_text,
            j.employment_type, j.work_model, j.seniority, j.industry,
            COALESCE(p.posted_at, j.posted_at),
            p.first_seen_at, p.last_seen_at, p.created_at, now()
        FROM _v04_split AS s
        JOIN job_source_postings AS p ON p.id = s.posting_id
        JOIN jobs AS j ON j.id = s.old_job_id
        """
    )
    op.execute(
        """
        INSERT INTO user_jobs (id, user_id, job_id, first_discovered_at, last_discovered_at)
        SELECT gen_random_uuid(), uj.user_id, s.new_job_id, uj.first_discovered_at,
               uj.last_discovered_at
        FROM _v04_split AS s
        JOIN user_jobs AS uj ON uj.job_id = s.old_job_id
        """
    )
    op.execute(
        """
        UPDATE job_source_postings AS p SET job_id = s.new_job_id
        FROM _v04_split AS s WHERE s.posting_id = p.id
        """
    )

    # --- Restore the V0.3 identity columns from each job's (now only) posting -----
    op.add_column('jobs', sa.Column('source', sa.String(length=100), nullable=True))
    op.add_column('jobs', sa.Column('source_job_id', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('dedup_key', sa.String(length=400), nullable=True))
    op.add_column('jobs', sa.Column('source_company_key', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('source_url', sa.String(length=2000), nullable=True))
    op.add_column('jobs', sa.Column('raw_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('jobs', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.add_column('jobs', sa.Column('normalizer_version', sa.String(length=50), nullable=True))
    op.add_column('jobs', sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE jobs AS j SET
            source = p.source, source_job_id = p.source_job_id, dedup_key = p.dedup_key,
            source_company_key = p.source_company_key, source_url = p.source_url,
            raw_data = p.raw_data, content_hash = p.content_hash,
            normalizer_version = p.normalizer_version, source_updated_at = p.source_updated_at
        FROM job_source_postings AS p WHERE p.job_id = j.id
        """
    )
    # A job without any posting can't exist in V0.4, but never lose a row over it.
    op.execute(
        """
        UPDATE jobs SET source = 'unknown', dedup_key = 'legacy:' || id::text
        WHERE dedup_key IS NULL
        """
    )
    op.alter_column('jobs', 'source', nullable=False)
    op.alter_column('jobs', 'dedup_key', nullable=False)
    op.create_unique_constraint('uq_jobs_dedup_key', 'jobs', ['dedup_key'])
    op.create_index('ix_jobs_source_source_job_id', 'jobs', ['source', 'source_job_id'])

    op.drop_index('ix_jobs_identity', table_name='jobs')
    op.drop_constraint('fk_jobs_preferred_source_posting_id', 'jobs', type_='foreignkey')
    op.drop_column('jobs', 'preferred_source_posting_id')
    for column in _NEW_JOB_COLUMNS:
        op.drop_column('jobs', column)

    op.drop_index('ix_job_source_postings_board', table_name='job_source_postings')
    op.drop_index('ix_job_source_postings_url_key', table_name='job_source_postings')
    op.drop_index('ix_job_source_postings_identity', table_name='job_source_postings')
    op.drop_index('uq_job_source_postings_source_job_id', table_name='job_source_postings')
    op.drop_index(op.f('ix_job_source_postings_job_id'), table_name='job_source_postings')
    op.drop_table('job_source_postings')
