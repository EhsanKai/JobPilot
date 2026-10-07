"""v0.10 browser automation

Revision ID: b4e8c2d6a1f9
Revises: a9d4f2b7c1e3
Create Date: 2026-10-08 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b4e8c2d6a1f9'
down_revision: Union[str, None] = 'a9d4f2b7c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - Purely additive: three new tables (automation_sessions, automation_fields,
#   automation_events) and three new enum types. No existing table, column,
#   constraint or row changes, so every V0-V0.9 row survives untouched and nothing
#   needs a backfill.
# - Ownership is enforced by composite foreign keys, the technique of V0.5-V0.9:
#   (application_id, user_id) -> applications (id, user_id) for sessions,
#   (session_id, user_id) -> automation_sessions (id, user_id) for fields and
#   events. A row cannot be stored for someone else's application or session.
# - A field's mapping to a question is a reference, not ownership: deleting a
#   question (V0.9) must not delete -- or be blocked by -- the field, so
#   question_id / proposed_question_id are plain ON DELETE SET NULL keys (a
#   composite key would also try to null the NOT NULL user_id). The service maps
#   fields only to questions of their own application.
# - uq_automation_sessions_one_live: at most one live session per application, so
#   two clicks (or two tabs) cannot open two browsers.
# - ck_automation_sessions_ended_iff_terminal: only STOPPED / FAILED sessions have
#   ended_at. There is deliberately no SUBMITTED status.
# - downgrade() drops exactly what was added (sessions, fields and events are lost);
#   the V0.9 schema and all V0-V0.9 data are restored exactly.

ENUMS = {
    'automation_status': ('STARTING', 'RUNNING', 'PAUSED', 'USER_CONTROL', 'BLOCKED', 'COMPLETED', 'STOPPED', 'FAILED'),
    'automation_mapping_status': ('UNMAPPED', 'AUTO_MAPPED', 'USER_CONFIRMED', 'NEEDS_REVIEW'),
    'automation_fill_status': ('NOT_FILLED', 'FILLED', 'FILL_FAILED', 'USER_MODIFIED', 'USER_ACTION_REQUIRED'),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    op.create_table('automation_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('status', _enum('automation_status'), nullable=False),
    sa.Column('target_url', sa.String(length=2000), nullable=False),
    sa.Column('current_url', sa.String(length=2000), nullable=True),
    sa.Column('page_title', sa.String(length=300), nullable=True),
    sa.Column('allowed_hosts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('attention_code', sa.String(length=40), nullable=True),
    sa.Column('browser_mode', sa.String(length=10), nullable=False),
    sa.Column('acknowledged_markers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('submit_detected', sa.Boolean(), nullable=False),
    sa.Column('last_error_code', sa.String(length=50), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status IN ('STOPPED', 'FAILED')) = (ended_at IS NOT NULL)", name='ck_automation_sessions_ended_iff_terminal'),
    sa.CheckConstraint("browser_mode IN ('visible', 'headless')", name='ck_automation_sessions_browser_mode'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_automation_sessions_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_automation_sessions_id_user_id')
    )
    op.create_index('ix_automation_sessions_application_created', 'automation_sessions', ['application_id', 'created_at'], unique=False)
    op.create_index('ix_automation_sessions_user_id', 'automation_sessions', ['user_id'], unique=False)
    op.create_index('uq_automation_sessions_one_live', 'automation_sessions', ['application_id'], unique=True, postgresql_where=sa.text("status IN ('STARTING', 'RUNNING', 'PAUSED', 'USER_CONTROL', 'BLOCKED', 'COMPLETED')"))
    op.create_table('automation_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('field_id', sa.UUID(), nullable=True),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id', 'user_id'], ['automation_sessions.id', 'automation_sessions.user_id'], name='fk_automation_events_session', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_automation_events_session_created', 'automation_events', ['session_id', 'created_at'], unique=False)
    op.create_index('ix_automation_events_user_id', 'automation_events', ['user_id'], unique=False)
    op.create_table('automation_fields',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('field_key', sa.String(length=255), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('input_type', sa.String(length=30), nullable=False),
    sa.Column('label', sa.String(length=500), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('dom_id', sa.String(length=255), nullable=True),
    sa.Column('form_context', sa.String(length=255), nullable=True),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.Column('max_length', sa.Integer(), nullable=True),
    sa.Column('options', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('locator', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('present', sa.Boolean(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_action_reason', sa.String(length=40), nullable=True),
    sa.Column('mapping_status', _enum('automation_mapping_status'), nullable=False),
    sa.Column('mapping_method', sa.String(length=20), nullable=True),
    sa.Column('mapping_confidence', sa.String(length=10), nullable=True),
    sa.Column('mapping_reason', sa.String(length=300), nullable=True),
    sa.Column('mapping_provenance', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('question_id', sa.UUID(), nullable=True),
    sa.Column('proposed_question_id', sa.UUID(), nullable=True),
    sa.Column('detected_document_type', sa.String(length=20), nullable=True),
    sa.Column('document_type', sa.String(length=20), nullable=True),
    sa.Column('document_id', sa.UUID(), nullable=True),
    sa.Column('document_confirmed', sa.Boolean(), nullable=False),
    sa.Column('fill_status', _enum('automation_fill_status'), nullable=False),
    sa.Column('fill_error_code', sa.String(length=50), nullable=True),
    sa.Column('last_agent_value_hash', sa.String(length=64), nullable=True),
    sa.Column('last_value_source', sa.String(length=20), nullable=True),
    sa.Column('last_filled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("mapping_confidence IS NULL OR mapping_confidence IN ('HIGH', 'MEDIUM', 'LOW')", name='ck_automation_fields_mapping_confidence'),
    sa.CheckConstraint("options IS NULL OR jsonb_typeof(options) = 'array'", name='ck_automation_fields_options_array'),
    sa.CheckConstraint('position >= 0', name='ck_automation_fields_position'),
    sa.ForeignKeyConstraint(['proposed_question_id'], ['application_questions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['question_id'], ['application_questions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id', 'user_id'], ['automation_sessions.id', 'automation_sessions.user_id'], name='fk_automation_fields_session', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_automation_fields_id_user_id'),
    sa.UniqueConstraint('session_id', 'field_key', name='uq_automation_fields_session_key')
    )
    op.create_index('ix_automation_fields_session_position', 'automation_fields', ['session_id', 'position'], unique=False)
    op.create_index(op.f('ix_automation_fields_question_id'), 'automation_fields', ['question_id'], unique=False)
    op.create_index('ix_automation_fields_user_id', 'automation_fields', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_automation_fields_user_id', table_name='automation_fields')
    op.drop_index(op.f('ix_automation_fields_question_id'), table_name='automation_fields')
    op.drop_index('ix_automation_fields_session_position', table_name='automation_fields')
    op.drop_table('automation_fields')
    op.drop_index('ix_automation_events_user_id', table_name='automation_events')
    op.drop_index('ix_automation_events_session_created', table_name='automation_events')
    op.drop_table('automation_events')
    op.drop_index('uq_automation_sessions_one_live', table_name='automation_sessions', postgresql_where=sa.text("status IN ('STARTING', 'RUNNING', 'PAUSED', 'USER_CONTROL', 'BLOCKED', 'COMPLETED')"))
    op.drop_index('ix_automation_sessions_user_id', table_name='automation_sessions')
    op.drop_index('ix_automation_sessions_application_created', table_name='automation_sessions')
    op.drop_table('automation_sessions')
    bind = op.get_bind()
    for name, values in ENUMS.items():
        sa.Enum(*values, name=name).drop(bind, checkfirst=True)
