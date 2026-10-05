"""v0.2 resume intelligence

Revision ID: a7c2e5d91f30
Revises: 06ce0b8897b3
Create Date: 2026-10-05 21:22:41.623266

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7c2e5d91f30'
down_revision: Union[str, None] = '06ce0b8897b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Postgres ENUM handling (lessons from V0/V0.1):
#
# - resumes.status moves from the V0 "resume_status" type (UPLOADED/
#   PROCESSING/PROCESSED/FAILED) to a *new, distinctly named*
#   "resume_processing_status" type with a different value set. The old name
#   is never reused with different values; it's dropped once nothing
#   references it, and downgrade() recreates it with its original values.
# - Values are converted with an explicit USING mapping, since Postgres
#   can't cast between two unrelated enum types on its own.
# - Every type this migration creates is created explicitly (checkfirst)
#   and dropped explicitly in downgrade(), because Alembic doesn't emit
#   DROP TYPE, and re-running upgrade() would otherwise fail with "type
#   already exists".

RESUME_PROCESSING_STATUS_VALUES = (
    'UPLOADED', 'EXTRACTING', 'EXTRACTED', 'PARSING', 'PARSED',
    'NEEDS_REVIEW', 'CONFIRMED', 'FAILED', 'OCR_REQUIRED',
)
LEGACY_RESUME_STATUS_VALUES = ('UPLOADED', 'PROCESSING', 'PROCESSED', 'FAILED')
RESUME_PROFILE_STATUS_VALUES = (
    'PROCESSING', 'NEEDS_REVIEW', 'CONFIRMED', 'SUPERSEDED', 'FAILED', 'OCR_REQUIRED',
)


def upgrade() -> None:
    bind = op.get_bind()

    # --- resumes.status: resume_status -> resume_processing_status --------
    sa.Enum(*RESUME_PROCESSING_STATUS_VALUES, name='resume_processing_status').create(
        bind, checkfirst=True
    )
    # V0 never actually processed resumes, so PROCESSING/PROCESSED rows can't
    # have a parsed result: they're mapped back to UPLOADED (ready to be
    # analyzed), never to a state that implies a reviewable profile exists.
    op.execute(
        """
        ALTER TABLE resumes
        ALTER COLUMN status TYPE resume_processing_status
        USING (
            CASE status::text
                WHEN 'FAILED' THEN 'FAILED'
                ELSE 'UPLOADED'
            END
        )::resume_processing_status
        """
    )
    sa.Enum(name='resume_status').drop(bind, checkfirst=True)

    # --- resumes: versioning, hashing, processing timestamps ---------------
    # version_number is NOT NULL: backfill existing rows per user in upload
    # order, then drop the temporary server default.
    op.add_column(
        'resumes',
        sa.Column('version_number', sa.Integer(), nullable=False, server_default='1'),
    )
    op.execute(
        """
        UPDATE resumes AS r
        SET version_number = ranked.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at, id) AS rn
            FROM resumes
        ) AS ranked
        WHERE r.id = ranked.id
        """
    )
    op.alter_column('resumes', 'version_number', server_default=None)
    op.add_column('resumes', sa.Column('file_hash', sa.String(length=64), nullable=True))
    op.add_column(
        'resumes', sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(op.f('ix_resumes_file_hash'), 'resumes', ['file_hash'], unique=False)
    op.create_unique_constraint('uq_resumes_user_version', 'resumes', ['user_id', 'version_number'])

    # --- resume_profiles ---------------------------------------------------
    sa.Enum(*RESUME_PROFILE_STATUS_VALUES, name='resume_profile_status').create(
        bind, checkfirst=True
    )
    op.create_table('resume_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('resume_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('status', postgresql.ENUM(*RESUME_PROFILE_STATUS_VALUES, name='resume_profile_status', create_type=False), nullable=False),
    sa.Column('parser_version', sa.String(length=50), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('source_file_hash', sa.String(length=64), nullable=True),
    sa.Column('ai_provider', sa.String(length=50), nullable=True),
    sa.Column('ai_model', sa.String(length=100), nullable=True),
    sa.Column('reused_from_profile_id', sa.UUID(), nullable=True),
    sa.Column('extraction_method', sa.String(length=50), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('extracted_text', sa.Text(), nullable=True),
    sa.Column('raw_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('reviewed_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['resume_id'], ['resumes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reused_from_profile_id'], ['resume_profiles.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resume_profiles_resume_id'), 'resume_profiles', ['resume_id'], unique=False)
    op.create_index(op.f('ix_resume_profiles_source_file_hash'), 'resume_profiles', ['source_file_hash'], unique=False)
    op.create_index(op.f('ix_resume_profiles_user_id'), 'resume_profiles', ['user_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(op.f('ix_resume_profiles_user_id'), table_name='resume_profiles')
    op.drop_index(op.f('ix_resume_profiles_source_file_hash'), table_name='resume_profiles')
    op.drop_index(op.f('ix_resume_profiles_resume_id'), table_name='resume_profiles')
    op.drop_table('resume_profiles')
    sa.Enum(name='resume_profile_status').drop(bind, checkfirst=True)

    op.drop_constraint('uq_resumes_user_version', 'resumes', type_='unique')
    op.drop_index(op.f('ix_resumes_file_hash'), table_name='resumes')
    op.drop_column('resumes', 'processing_started_at')
    op.drop_column('resumes', 'file_hash')
    op.drop_column('resumes', 'version_number')

    # Recreate the V0 type with its original values and map back. Analyzed
    # resumes become PROCESSED (V0's closest meaning); in-flight ones become
    # PROCESSING; OCR_REQUIRED is a failure from V0's point of view.
    sa.Enum(*LEGACY_RESUME_STATUS_VALUES, name='resume_status').create(bind, checkfirst=True)
    op.execute(
        """
        ALTER TABLE resumes
        ALTER COLUMN status TYPE resume_status
        USING (
            CASE status::text
                WHEN 'UPLOADED' THEN 'UPLOADED'
                WHEN 'EXTRACTING' THEN 'PROCESSING'
                WHEN 'EXTRACTED' THEN 'PROCESSING'
                WHEN 'PARSING' THEN 'PROCESSING'
                WHEN 'PARSED' THEN 'PROCESSING'
                WHEN 'NEEDS_REVIEW' THEN 'PROCESSED'
                WHEN 'CONFIRMED' THEN 'PROCESSED'
                ELSE 'FAILED'
            END
        )::resume_status
        """
    )
    sa.Enum(name='resume_processing_status').drop(bind, checkfirst=True)
