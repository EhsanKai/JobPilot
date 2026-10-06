"""v0.9 application questions, document origin and CV photo

Revision ID: a9d4f2b7c1e3
Revises: f1a6d3c8b2e4
Create Date: 2026-10-07 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a9d4f2b7c1e3'
down_revision: Union[str, None] = 'f1a6d3c8b2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - Purely additive. Four new tables (application_questions,
#   application_question_answers, application_question_generations,
#   application_photos), five new enum types, and three new *nullable or
#   defaulted* columns on generated_documents (prompt_hash, origin,
#   base_document_id). No existing column, row, constraint or index changes, so
#   every V0-V0.8 row survives untouched and nothing needs a backfill: existing
#   documents become origin = 'AI' (the column default) with no base document,
#   which is exactly what they are.
# - Ownership is enforced by composite foreign keys, the technique of V0.5-V0.8:
#   (application_id, user_id) -> applications (id, user_id) for questions and
#   photos, (question_id, user_id) -> application_questions (id, user_id) for
#   answers and attempts. A row cannot be stored for someone else's application.
# - application_question_answers holds the *current* state, one row per question
#   (unique question_id). Check constraints keep the user's final answer and the
#   AI suggestion coherent with the status (a final answer exists exactly when
#   the user accepted or wrote one). application_question_generations is the
#   write-once history of attempts; the answer points at the attempt of its
#   current suggestion and at the attempt its final answer was based on
#   (SET NULL: those pointers are provenance, never a reason to block a delete).
#   generated_documents.base_document_id is deliberately NO ACTION, not SET NULL: a
#   derived version without a base would violate ck_generated_documents_base_iff_derived,
#   and no version is ever deleted on its own (only with its application, together).
# - ck_generated_documents_base_iff_derived: a humanized or user-edited version
#   always names the version it was made from, an AI version never does. Alembic
#   does not detect check constraints, so it is created here explicitly.
# - downgrade() drops exactly what was added: questions, answers, attempts and
#   photo records are lost; the V0.8 schema and all V0-V0.8 data are restored
#   exactly. (A stored photo file stays in storage; the downgrade only removes
#   the table that referenced it.)

QUESTION_TYPE_VALUES = ('FREE_TEXT', 'YES_NO', 'SINGLE_CHOICE', 'MULTI_CHOICE', 'NUMERIC', 'DATE', 'SALARY', 'UNKNOWN')
ANSWER_STATUS_VALUES = ('NOT_STARTED', 'NEEDS_INPUT', 'SUGGESTED', 'ACCEPTED', 'USER_EDITED', 'REJECTED')
ANSWER_SOURCE_VALUES = ('DETERMINISTIC', 'AI', 'USER', 'HYBRID')
GENERATION_METHOD_VALUES = ('DETERMINISTIC', 'AI')
GENERATION_OUTCOME_VALUES = ('SUGGESTED', 'NEEDS_INPUT', 'REJECTED', 'FAILED')
DOCUMENT_ORIGIN_VALUES = ('AI', 'HUMANIZED', 'USER_EDITED')

ENUMS = {
    'application_question_type': QUESTION_TYPE_VALUES,
    'application_answer_status': ANSWER_STATUS_VALUES,
    'application_answer_source': ANSWER_SOURCE_VALUES,
    'application_generation_method': GENERATION_METHOD_VALUES,
    'application_generation_outcome': GENERATION_OUTCOME_VALUES,
    'generated_document_origin': DOCUMENT_ORIGIN_VALUES,
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    op.create_table('application_photos',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('storage_reference', sa.String(length=500), nullable=False),
    sa.Column('content_type', sa.String(length=50), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('size_bytes > 0', name='ck_application_photos_size'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_application_photos_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', name='uq_application_photos_application_id')
    )
    op.create_index(op.f('ix_application_photos_user_id'), 'application_photos', ['user_id'], unique=False)
    op.create_table('application_questions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('question_text', sa.Text(), nullable=False),
    sa.Column('normalized_question_text', sa.Text(), nullable=False),
    sa.Column('question_hash', sa.String(length=64), nullable=False),
    sa.Column('question_type', _enum('application_question_type'), nullable=False),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.Column('max_length', sa.Integer(), nullable=True),
    sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('external_key', sa.String(length=255), nullable=True),
    sa.Column('field_name', sa.String(length=255), nullable=True),
    sa.Column('source', sa.String(length=50), nullable=True),
    sa.Column('source_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("options IS NULL OR jsonb_typeof(options) = 'array'", name='ck_application_questions_options_array'),
    sa.CheckConstraint("question_type NOT IN ('SINGLE_CHOICE', 'MULTI_CHOICE') OR options IS NOT NULL", name='ck_application_questions_choice_has_options'),
    sa.CheckConstraint('max_length IS NULL OR max_length >= 1', name='ck_application_questions_max_length'),
    sa.CheckConstraint('sort_order >= 0', name='ck_application_questions_sort_order'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_application_questions_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_application_questions_id_user_id')
    )
    op.create_index('ix_application_questions_application_sort', 'application_questions', ['application_id', 'sort_order', 'created_at'], unique=False)
    op.create_index(op.f('ix_application_questions_user_id'), 'application_questions', ['user_id'], unique=False)
    op.create_index('uq_application_questions_external_key', 'application_questions', ['application_id', 'external_key'], unique=True, postgresql_where=sa.text('external_key IS NOT NULL'))
    op.create_table('application_question_generations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('question_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('method', _enum('application_generation_method'), nullable=False),
    sa.Column('outcome', _enum('application_generation_outcome'), nullable=False),
    sa.Column('needs_input_reason', sa.String(length=50), nullable=True),
    sa.Column('suggestion', sa.Text(), nullable=True),
    sa.Column('selected_options', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('source_fact_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source_facts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('confidence', sa.String(length=10), nullable=True),
    sa.Column('validation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.Column('question_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('max_length', sa.Integer(), nullable=True),
    sa.Column('prompt_version', sa.String(length=50), nullable=True),
    sa.Column('prompt_hash', sa.String(length=64), nullable=True),
    sa.Column('ai_provider', sa.String(length=50), nullable=True),
    sa.Column('ai_model', sa.String(length=100), nullable=True),
    sa.Column('resume_id', sa.UUID(), nullable=True),
    sa.Column('resume_profile_id', sa.UUID(), nullable=True),
    sa.Column('resume_version_number', sa.Integer(), nullable=True),
    sa.Column('application_context_snapshot_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("method <> 'AI' OR (prompt_version IS NOT NULL AND prompt_hash IS NOT NULL AND ai_provider IS NOT NULL)", name='ck_application_question_generations_ai_provenance'),
    sa.CheckConstraint("outcome <> 'SUGGESTED' OR (suggestion IS NOT NULL OR selected_options IS NOT NULL)", name='ck_application_question_generations_suggested_has_value'),
    sa.CheckConstraint("outcome = 'SUGGESTED' OR (suggestion IS NULL AND selected_options IS NULL)", name='ck_application_question_generations_only_suggested_has_value'),
    sa.ForeignKeyConstraint(['application_context_snapshot_id'], ['application_context_snapshots.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['question_id', 'user_id'], ['application_questions.id', 'application_questions.user_id'], name='fk_application_question_generations_question', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resume_id'], ['resumes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resume_profile_id'], ['resume_profiles.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_application_question_generations_question_created', 'application_question_generations', ['question_id', 'created_at'], unique=False)
    op.create_index('ix_application_question_generations_user_id', 'application_question_generations', ['user_id'], unique=False)
    op.create_table('application_question_answers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('question_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('status', _enum('application_answer_status'), nullable=False),
    sa.Column('answer_source', _enum('application_answer_source'), nullable=True),
    sa.Column('ai_suggestion', sa.Text(), nullable=True),
    sa.Column('ai_selected_options', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ai_source_fact_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ai_provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ai_generation_id', sa.UUID(), nullable=True),
    sa.Column('suggestion_dismissed', sa.Boolean(), nullable=False),
    sa.Column('needs_input_reason', sa.String(length=50), nullable=True),
    sa.Column('final_answer', sa.Text(), nullable=True),
    sa.Column('final_selected_options', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('basis_generation_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status IN ('ACCEPTED', 'USER_EDITED')) = (final_answer IS NOT NULL OR final_selected_options IS NOT NULL)", name='ck_application_question_answers_final_iff_decided'),
    sa.CheckConstraint("status <> 'ACCEPTED' OR answer_source IN ('DETERMINISTIC', 'AI')", name='ck_application_question_answers_accepted_source'),
    sa.CheckConstraint("status <> 'NEEDS_INPUT' OR (NOT (ai_suggestion IS NOT NULL OR ai_selected_options IS NOT NULL) AND NOT (final_answer IS NOT NULL OR final_selected_options IS NOT NULL))", name='ck_application_question_answers_needs_input_empty'),
    sa.CheckConstraint("status <> 'USER_EDITED' OR answer_source IN ('USER', 'HYBRID')", name='ck_application_question_answers_edited_source'),
    sa.CheckConstraint("status NOT IN ('SUGGESTED', 'REJECTED') OR (ai_suggestion IS NOT NULL OR ai_selected_options IS NOT NULL)", name='ck_application_question_answers_suggestion_exists'),
    sa.CheckConstraint('(answer_source IS NOT NULL) = (final_answer IS NOT NULL OR final_selected_options IS NOT NULL)', name='ck_application_question_answers_source_iff_final'),
    sa.ForeignKeyConstraint(['ai_generation_id'], ['application_question_generations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['basis_generation_id'], ['application_question_generations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['question_id', 'user_id'], ['application_questions.id', 'application_questions.user_id'], name='fk_application_question_answers_question', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('question_id', name='uq_application_question_answers_question_id')
    )
    op.create_index('ix_application_question_answers_user_id', 'application_question_answers', ['user_id'], unique=False)
    op.add_column('generated_documents', sa.Column('prompt_hash', sa.String(length=64), nullable=True))
    op.add_column('generated_documents', sa.Column('origin', _enum('generated_document_origin'), server_default='AI', nullable=False))
    op.add_column('generated_documents', sa.Column('base_document_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_generated_documents_base_document', 'generated_documents', 'generated_documents', ['base_document_id'], ['id'])
    op.create_check_constraint('ck_generated_documents_base_iff_derived', 'generated_documents', "(origin = 'AI') = (base_document_id IS NULL)")


def downgrade() -> None:
    op.drop_constraint('ck_generated_documents_base_iff_derived', 'generated_documents', type_='check')
    op.drop_constraint('fk_generated_documents_base_document', 'generated_documents', type_='foreignkey')
    op.drop_column('generated_documents', 'base_document_id')
    op.drop_column('generated_documents', 'origin')
    op.drop_column('generated_documents', 'prompt_hash')
    op.drop_index('ix_application_question_answers_user_id', table_name='application_question_answers')
    op.drop_table('application_question_answers')
    op.drop_index('ix_application_question_generations_user_id', table_name='application_question_generations')
    op.drop_index('ix_application_question_generations_question_created', table_name='application_question_generations')
    op.drop_table('application_question_generations')
    op.drop_index('uq_application_questions_external_key', table_name='application_questions', postgresql_where=sa.text('external_key IS NOT NULL'))
    op.drop_index(op.f('ix_application_questions_user_id'), table_name='application_questions')
    op.drop_index('ix_application_questions_application_sort', table_name='application_questions')
    op.drop_table('application_questions')
    op.drop_index(op.f('ix_application_photos_user_id'), table_name='application_photos')
    op.drop_table('application_photos')
    bind = op.get_bind()
    for name, values in ENUMS.items():
        sa.Enum(*values, name=name).drop(bind, checkfirst=True)
