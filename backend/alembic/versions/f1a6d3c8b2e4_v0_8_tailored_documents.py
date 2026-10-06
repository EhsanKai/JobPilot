"""v0.8 tailored documents

Revision ID: f1a6d3c8b2e4
Revises: e8b3c1a5d7f2
Create Date: 2026-10-06 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1a6d3c8b2e4'
down_revision: Union[str, None] = 'e8b3c1a5d7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - Purely additive: one new table (generated_documents), two new enum types and
#   one new unique constraint on applications (id, user_id) -- trivially satisfied
#   because id is the primary key -- which is the target of the table's composite
#   ownership foreign key (the same technique as V0.5's job_matches and V0.7's
#   applications). No existing table, column, row or index is changed, so every
#   V0-V0.7 row survives untouched and nothing needs a backfill: applications
#   simply have no documents until the user generates one.
# - A generated document is one generation *attempt*. Only a VALIDATED attempt is
#   a document: database checks tie both `version_number` and `content` to
#   status = 'VALIDATED', so output that failed truth validation can never be
#   stored as a usable document. Versions (1, 2, 3...) are unique per application
#   and type; a partial unique index allows one in-flight (GENERATING) attempt per
#   application and type.
# - resume_id / resume_profile_id are SET NULL (users can delete resumes; the
#   document stays, with resume_version_number as the surviving provenance). The
#   application context snapshot reference cascades: a snapshot only ever goes
#   away together with its application.
# - downgrade() drops everything V0.8 added. Generated documents are lost; the
#   V0.7 schema and all V0-V0.7 data are restored exactly.

DOCUMENT_TYPE_VALUES = ('TAILORED_CV', 'COVER_LETTER')
DOCUMENT_STATUS_VALUES = ('GENERATING', 'VALIDATED', 'REJECTED', 'FAILED')


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*DOCUMENT_TYPE_VALUES, name='generated_document_type').create(bind, checkfirst=True)
    sa.Enum(*DOCUMENT_STATUS_VALUES, name='generated_document_status').create(bind, checkfirst=True)

    op.create_unique_constraint('uq_applications_id_user_id', 'applications', ['id', 'user_id'])

    op.create_table('generated_documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('document_type', postgresql.ENUM(*DOCUMENT_TYPE_VALUES, name='generated_document_type', create_type=False), nullable=False),
    sa.Column('status', postgresql.ENUM(*DOCUMENT_STATUS_VALUES, name='generated_document_status', create_type=False), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=True),
    sa.Column('language', sa.String(length=2), nullable=False),
    sa.Column('resume_id', sa.UUID(), nullable=True),
    sa.Column('resume_profile_id', sa.UUID(), nullable=True),
    sa.Column('resume_version_number', sa.Integer(), nullable=True),
    sa.Column('application_context_snapshot_id', sa.UUID(), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=50), nullable=False),
    sa.Column('ai_provider', sa.String(length=50), nullable=False),
    sa.Column('ai_model', sa.String(length=100), nullable=True),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('source_facts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('validation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'VALIDATED') = (version_number IS NOT NULL)", name='ck_generated_documents_version_iff_validated'),
    sa.CheckConstraint("(status = 'VALIDATED') = (content IS NOT NULL)", name='ck_generated_documents_content_iff_validated'),
    sa.CheckConstraint('version_number IS NULL OR version_number >= 1', name='ck_generated_documents_version_number'),
    sa.CheckConstraint("language ~ '^[a-z]{2}$'", name='ck_generated_documents_language'),
    sa.ForeignKeyConstraint(['application_context_snapshot_id'], ['application_context_snapshots.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_generated_documents_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resume_id'], ['resumes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resume_profile_id'], ['resume_profiles.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', 'document_type', 'version_number', name='uq_generated_documents_version'),
    )
    op.create_index('uq_generated_documents_one_in_flight', 'generated_documents', ['application_id', 'document_type'], unique=True, postgresql_where=sa.text("status = 'GENERATING'"))
    op.create_index('ix_generated_documents_application_type_created', 'generated_documents', ['application_id', 'document_type', 'created_at'], unique=False)
    op.create_index(op.f('ix_generated_documents_user_id'), 'generated_documents', ['user_id'], unique=False)
    op.create_index(op.f('ix_generated_documents_resume_id'), 'generated_documents', ['resume_id'], unique=False)
    op.create_index(op.f('ix_generated_documents_resume_profile_id'), 'generated_documents', ['resume_profile_id'], unique=False)
    op.create_index(op.f('ix_generated_documents_application_context_snapshot_id'), 'generated_documents', ['application_context_snapshot_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table('generated_documents')
    op.drop_constraint('uq_applications_id_user_id', 'applications', type_='unique')
    sa.Enum(name='generated_document_status').drop(bind, checkfirst=True)
    sa.Enum(name='generated_document_type').drop(bind, checkfirst=True)
