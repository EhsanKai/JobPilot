"""v0.6 job matching & explainability

Revision ID: c7d2e9f4b1a6
Revises: a56b296b53a5
Create Date: 2026-10-06 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c7d2e9f4b1a6'
down_revision: Union[str, None] = 'a56b296b53a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - Why a migration at all: V0.6 is the first writer of the V0.5 tables and
#   needs (1) the two separate sub-scores of a result -- Job Match and
#   Preference Match -- with their coverage, the taxonomy version and the
#   structured evidence on job_matches; (2) a place for validated AI
#   explanations (job_match_explanations); (3) a requirement kind for "asked
#   for, but the posting doesn't say whether required or preferred"
#   (job_requirement_kind 'UNSPECIFIED'), which must never be guessed as
#   either.
# - Additive only: new nullable columns (evidence defaults to '{}'), new
#   CHECK constraints that existing rows satisfy, one new table, one new enum
#   value. No existing column, index or row is changed.
# - No foreign key from job_matches to user_jobs: results are keyed by
#   (user_id, job_id), the natural key of user_jobs, and the service only
#   computes and reads them through the user's UserJob.
# - downgrade(): drops the table and columns. Postgres can't drop an enum
#   value, so the type is recreated without 'UNSPECIFIED'; the derived rows
#   that use it (job_intelligence versions with UNSPECIFIED requirements and
#   the matches computed from them) are deleted first. They are derived data
#   and are recomputed by V0.6 on the next "Calculate match".

JOB_REQUIREMENT_KIND_V05 = ('REQUIRED', 'PREFERRED', 'CONTEXTUAL')


def upgrade() -> None:
    op.execute("ALTER TYPE job_requirement_kind ADD VALUE IF NOT EXISTS 'UNSPECIFIED'")

    op.add_column('job_matches', sa.Column('job_score', sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column('job_matches', sa.Column('preference_score', sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column('job_matches', sa.Column('job_coverage', sa.Numeric(precision=4, scale=3), nullable=True))
    op.add_column('job_matches', sa.Column('preference_coverage', sa.Numeric(precision=4, scale=3), nullable=True))
    op.add_column('job_matches', sa.Column('taxonomy_version', sa.String(length=50), nullable=True))
    op.add_column('job_matches', sa.Column(
        'evidence', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
        nullable=False,
    ))
    op.create_check_constraint(
        'ck_job_matches_job_score', 'job_matches',
        'job_score IS NULL OR (job_score >= 0 AND job_score <= 100)',
    )
    op.create_check_constraint(
        'ck_job_matches_preference_score', 'job_matches',
        'preference_score IS NULL OR (preference_score >= 0 AND preference_score <= 100)',
    )
    op.create_check_constraint(
        'ck_job_matches_job_coverage', 'job_matches',
        'job_coverage IS NULL OR (job_coverage >= 0 AND job_coverage <= 1)',
    )
    op.create_check_constraint(
        'ck_job_matches_preference_coverage', 'job_matches',
        'preference_coverage IS NULL OR (preference_coverage >= 0 AND preference_coverage <= 1)',
    )

    op.create_table('job_match_explanations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_match_id', sa.UUID(), nullable=False),
    sa.Column('prompt_version', sa.String(length=50), nullable=False),
    sa.Column('ai_provider', sa.String(length=50), nullable=False),
    sa.Column('ai_model', sa.String(length=100), nullable=True),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_match_id'], ['job_matches.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_match_id', 'prompt_version', name='uq_job_match_explanations_match_prompt'),
    )


def downgrade() -> None:
    op.drop_table('job_match_explanations')

    op.drop_constraint('ck_job_matches_preference_coverage', 'job_matches', type_='check')
    op.drop_constraint('ck_job_matches_job_coverage', 'job_matches', type_='check')
    op.drop_constraint('ck_job_matches_preference_score', 'job_matches', type_='check')
    op.drop_constraint('ck_job_matches_job_score', 'job_matches', type_='check')
    op.drop_column('job_matches', 'evidence')
    op.drop_column('job_matches', 'taxonomy_version')
    op.drop_column('job_matches', 'preference_coverage')
    op.drop_column('job_matches', 'job_coverage')
    op.drop_column('job_matches', 'preference_score')
    op.drop_column('job_matches', 'job_score')

    # Derived rows that use the new kind can't exist in V0.5's type.
    op.execute(
        "DELETE FROM job_matches WHERE job_intelligence_id IN "
        "(SELECT job_intelligence_id FROM job_requirements WHERE kind = 'UNSPECIFIED')"
    )
    op.execute(
        "DELETE FROM job_intelligence WHERE id IN "
        "(SELECT job_intelligence_id FROM job_requirements WHERE kind = 'UNSPECIFIED')"
    )
    op.execute("ALTER TYPE job_requirement_kind RENAME TO job_requirement_kind_v06")
    values = ", ".join(f"'{value}'" for value in JOB_REQUIREMENT_KIND_V05)
    op.execute(f"CREATE TYPE job_requirement_kind AS ENUM ({values})")
    op.execute(
        "ALTER TABLE job_requirements ALTER COLUMN kind TYPE job_requirement_kind "
        "USING kind::text::job_requirement_kind"
    )
    op.execute("DROP TYPE job_requirement_kind_v06")
