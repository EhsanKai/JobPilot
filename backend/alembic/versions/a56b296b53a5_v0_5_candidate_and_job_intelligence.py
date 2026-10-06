"""v0.5 candidate & job intelligence data model

Revision ID: a56b296b53a5
Revises: d4a7e3b9c2f1
Create Date: 2026-10-06 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a56b296b53a5'
down_revision: Union[str, None] = 'd4a7e3b9c2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - Create-only: four new tables (candidate_profile_versions,
#   job_intelligence, job_requirements, job_matches) and their indexes. No
#   existing table, column, index or enum is altered, and no data is
#   written -- nothing exists to backfill yet.
# - Five new enum types are created explicitly and dropped explicitly in
#   downgrade() (Alembic emits no DROP TYPE; see the V0.2 notes).
#   job_requirements.salary_period reuses the existing 'salary_period' type
#   (create_type=False): downgrade() leaves it alone.
# - job_matches references candidate_profile_versions / job_intelligence
#   through composite (id, user_id) / (id, job_id) foreign keys without an
#   ON DELETE action: a version a match references can't be deleted on its
#   own, while deleting a user or a job still cascades to everything.
# - downgrade() drops exactly these objects, in dependency order.

CANDIDATE_PROFILE_VERSION_REASON_VALUES = (
    'INITIAL', 'PROFILE_UPDATED', 'PREFERENCES_UPDATED', 'RESUME_CONFIRMED', 'SCHEMA_UPGRADE',
    'TAXONOMY_UPGRADE',
)
JOB_REQUIREMENT_KIND_VALUES = ('REQUIRED', 'PREFERRED', 'CONTEXTUAL')
JOB_REQUIREMENT_CATEGORY_VALUES = (
    'SKILL', 'EXPERIENCE', 'LANGUAGE', 'EDUCATION', 'CERTIFICATION', 'SENIORITY', 'WORK_MODEL',
    'EMPLOYMENT_TYPE', 'SALARY', 'AUTHORIZATION', 'TRAVEL',
)
EXTRACTION_CONFIDENCE_VALUES = ('HIGH', 'MEDIUM', 'LOW')
JOB_MATCH_VERDICT_VALUES = ('STRONG', 'GOOD', 'PARTIAL', 'WEAK', 'BLOCKED', 'INSUFFICIENT_DATA')
# Existing type (V0.3), reused.
SALARY_PERIOD_VALUES = ('YEAR', 'MONTH', 'WEEK', 'DAY', 'HOUR')

NEW_ENUMS = {
    'candidate_profile_version_reason': CANDIDATE_PROFILE_VERSION_REASON_VALUES,
    'job_requirement_kind': JOB_REQUIREMENT_KIND_VALUES,
    'job_requirement_category': JOB_REQUIREMENT_CATEGORY_VALUES,
    'extraction_confidence': EXTRACTION_CONFIDENCE_VALUES,
    'job_match_verdict': JOB_MATCH_VERDICT_VALUES,
}


def _enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in NEW_ENUMS.items():
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    # --- candidate_profile_versions ----------------------------------------------
    op.create_table('candidate_profile_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('schema_version', sa.String(length=50), nullable=False),
    sa.Column('taxonomy_version', sa.String(length=50), nullable=True),
    sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reason', _enum('candidate_profile_version_reason', CANDIDATE_PROFILE_VERSION_REASON_VALUES), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('version_number >= 1', name='ck_candidate_profile_versions_version_number'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_candidate_profile_versions_id_user'),
    sa.UniqueConstraint('user_id', 'content_hash', name='uq_candidate_profile_versions_user_content_hash'),
    sa.UniqueConstraint('user_id', 'version_number', name='uq_candidate_profile_versions_user_version')
    )

    # --- job_intelligence --------------------------------------------------------
    op.create_table('job_intelligence',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('extractor_version', sa.String(length=50), nullable=False),
    sa.Column('taxonomy_version', sa.String(length=50), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('result_hash', sa.String(length=64), nullable=False),
    sa.Column('facts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_posting_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_posting_id'], ['job_source_postings.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'job_id', name='uq_job_intelligence_id_job'),
    sa.UniqueConstraint('job_id', 'result_hash', 'extractor_version', 'taxonomy_version', name='uq_job_intelligence_result')
    )
    op.create_index(
        'uq_job_intelligence_current', 'job_intelligence', ['job_id'],
        unique=True, postgresql_where=sa.text('is_current'),
    )
    op.create_index(
        op.f('ix_job_intelligence_source_posting_id'), 'job_intelligence', ['source_posting_id'],
    )

    # --- job_requirements --------------------------------------------------------
    op.create_table('job_requirements',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_intelligence_id', sa.UUID(), nullable=False),
    sa.Column('kind', _enum('job_requirement_kind', JOB_REQUIREMENT_KIND_VALUES), nullable=False),
    sa.Column('category', _enum('job_requirement_category', JOB_REQUIREMENT_CATEGORY_VALUES), nullable=False),
    sa.Column('skill_key', sa.String(length=100), nullable=True),
    sa.Column('value_key', sa.String(length=100), nullable=True),
    sa.Column('value_text', sa.String(length=500), nullable=True),
    sa.Column('level', sa.String(length=50), nullable=True),
    sa.Column('min_years', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('salary_min', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('salary_max', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('salary_currency', sa.String(length=3), nullable=True),
    sa.Column('salary_period', _enum('salary_period', SALARY_PERIOD_VALUES), nullable=True),
    sa.Column('source_field', sa.String(length=50), nullable=False),
    sa.Column('section_kind', sa.String(length=50), nullable=True),
    sa.Column('extraction_method', sa.String(length=50), nullable=False),
    sa.Column('confidence', _enum('extraction_confidence', EXTRACTION_CONFIDENCE_VALUES), nullable=False),
    sa.Column('evidence_text', sa.String(length=600), nullable=True),
    sa.Column('evidence_offset', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("skill_key IS NULL OR category = 'SKILL'", name='ck_job_requirements_skill_key'),
    sa.CheckConstraint(
        "category = 'SALARY' OR (salary_min IS NULL AND salary_max IS NULL "
        "AND salary_currency IS NULL AND salary_period IS NULL)",
        name='ck_job_requirements_salary_fields',
    ),
    sa.CheckConstraint(
        'salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max',
        name='ck_job_requirements_salary_range',
    ),
    sa.CheckConstraint('min_years IS NULL OR min_years >= 0', name='ck_job_requirements_min_years'),
    sa.CheckConstraint(
        'evidence_offset IS NULL OR (evidence_offset >= 0 AND evidence_text IS NOT NULL)',
        name='ck_job_requirements_evidence_offset',
    ),
    sa.ForeignKeyConstraint(['job_intelligence_id'], ['job_intelligence.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        op.f('ix_job_requirements_job_intelligence_id'), 'job_requirements', ['job_intelligence_id'],
    )

    # --- job_matches -------------------------------------------------------------
    op.create_table('job_matches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('candidate_profile_version_id', sa.UUID(), nullable=False),
    sa.Column('job_intelligence_id', sa.UUID(), nullable=False),
    sa.Column('model_id', sa.String(length=50), nullable=False),
    sa.Column('model_hash', sa.String(length=64), nullable=False),
    sa.Column('score', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('coverage', sa.Numeric(precision=4, scale=3), nullable=False),
    sa.Column('verdict', _enum('job_match_verdict', JOB_MATCH_VERDICT_VALUES), nullable=False),
    sa.Column('is_blocked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('criteria', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('caps', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('score IS NULL OR (score >= 0 AND score <= 100)', name='ck_job_matches_score'),
    sa.CheckConstraint('coverage >= 0 AND coverage <= 1', name='ck_job_matches_coverage'),
    sa.CheckConstraint("is_blocked = (verdict = 'BLOCKED')", name='ck_job_matches_blocked'),
    sa.ForeignKeyConstraint(
        ['candidate_profile_version_id', 'user_id'],
        ['candidate_profile_versions.id', 'candidate_profile_versions.user_id'],
        name='fk_job_matches_candidate_profile_version',
    ),
    sa.ForeignKeyConstraint(
        ['job_intelligence_id', 'job_id'],
        ['job_intelligence.id', 'job_intelligence.job_id'],
        name='fk_job_matches_job_intelligence',
    ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'user_id', 'job_id', 'candidate_profile_version_id', 'job_intelligence_id', 'model_id',
        name='uq_job_matches_inputs',
    )
    )
    op.create_index(
        'uq_job_matches_current', 'job_matches', ['user_id', 'job_id'],
        unique=True, postgresql_where=sa.text('is_current'),
    )
    op.create_index(
        'ix_job_matches_user_current_score', 'job_matches',
        ['user_id', sa.text('score DESC NULLS LAST')],
        postgresql_where=sa.text('is_current'),
    )
    op.create_index(op.f('ix_job_matches_job_id'), 'job_matches', ['job_id'])
    op.create_index(
        op.f('ix_job_matches_candidate_profile_version_id'), 'job_matches',
        ['candidate_profile_version_id'],
    )
    op.create_index(
        op.f('ix_job_matches_job_intelligence_id'), 'job_matches', ['job_intelligence_id'],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(op.f('ix_job_matches_job_intelligence_id'), table_name='job_matches')
    op.drop_index(op.f('ix_job_matches_candidate_profile_version_id'), table_name='job_matches')
    op.drop_index(op.f('ix_job_matches_job_id'), table_name='job_matches')
    op.drop_index('ix_job_matches_user_current_score', table_name='job_matches')
    op.drop_index('uq_job_matches_current', table_name='job_matches')
    op.drop_table('job_matches')

    op.drop_index(op.f('ix_job_requirements_job_intelligence_id'), table_name='job_requirements')
    op.drop_table('job_requirements')

    op.drop_index(op.f('ix_job_intelligence_source_posting_id'), table_name='job_intelligence')
    op.drop_index('uq_job_intelligence_current', table_name='job_intelligence')
    op.drop_table('job_intelligence')

    op.drop_table('candidate_profile_versions')

    # 'salary_period' is pre-existing and stays.
    for name in reversed(list(NEW_ENUMS)):
        sa.Enum(name=name).drop(bind, checkfirst=True)
