"""v0.11 review and submit

Revision ID: c5f2a8e1d9b3
Revises: b4e8c2d6a1f9
Create Date: 2026-10-09 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c5f2a8e1d9b3'
down_revision: Union[str, None] = 'b4e8c2d6a1f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - Purely additive: four new tables (automation_final_reviews,
#   automation_consent_authorizations, automation_submission_authorizations,
#   automation_submission_attempts), three new enum types, and one nullable column
#   (automation_fields.consent_text). No existing row changes; nothing needs a backfill.
# - Ownership: composite foreign keys, as in V0.5-V0.10 -- (application_id, user_id) ->
#   applications, (session_id, user_id) -> automation_sessions, (field_id, user_id) ->
#   automation_fields, (final_review_id, user_id) and (authorization_id, user_id) to the
#   new tables. A review, consent, authorization or attempt cannot be stored for someone
#   else's application, session or field.
# - Exactly once, in the database itself:
#     uq_automation_submission_attempts_authorization   one attempt per authorization
#     uq_automation_submission_attempts_one_running     one unfinished attempt per application
#     uq_automation_submission_authorizations_one_open  one open authorization per application
#     uq_automation_submission_authorizations_review    a review authorizes at most once
#     uq_automation_final_reviews_one_active            one current review per session
#     uq_automation_consent_authorizations_one_live     one live authorization per consent
# - ck_automation_submission_attempts_pressed / _failed_not_pressed: a SUCCESS or
#   SUBMISSION_UNKNOWN always has submit_triggered_at; a FAILED_BEFORE_SUBMIT never has.
# - The application_status enum already has 'submitted' (V0): a confirmed submission uses
#   it; no enum change.
# - downgrade() drops exactly what was added (reviews, consents, authorizations and
#   attempts are lost); the V0.10 schema and all V0-V0.10 data are restored exactly.

ENUMS = {
    'final_review_status': ('ACTIVE', 'INVALIDATED', 'SUPERSEDED', 'USED'),
    'submission_authorization_status': ('AUTHORIZED', 'CONSUMED', 'EXPIRED', 'INVALIDATED'),
    'submission_result': ('SUCCESS', 'FAILED_BEFORE_SUBMIT', 'SUBMISSION_UNKNOWN'),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    op.create_table('automation_final_reviews',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('state_hash', sa.String(length=64), nullable=False),
    sa.Column('destination_host', sa.String(length=255), nullable=False),
    sa.Column('destination_domain', sa.String(length=255), nullable=False),
    sa.Column('destination_url', sa.String(length=2000), nullable=False),
    sa.Column('page_marker', sa.String(length=64), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', _enum('final_review_status'), nullable=False),
    sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('invalidation_reason', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'INVALIDATED') = (invalidated_at IS NOT NULL)", name='ck_automation_final_reviews_invalidated'),
    sa.CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name='ck_automation_final_reviews_state_hash'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_automation_final_reviews_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id', 'user_id'], ['automation_sessions.id', 'automation_sessions.user_id'], name='fk_automation_final_reviews_session', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_automation_final_reviews_id_user_id')
    )
    op.create_index('ix_automation_final_reviews_application', 'automation_final_reviews', ['application_id', 'created_at'], unique=False)
    op.create_index('ix_automation_final_reviews_user_id', 'automation_final_reviews', ['user_id'], unique=False)
    op.create_index('uq_automation_final_reviews_one_active', 'automation_final_reviews', ['session_id'], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"))
    op.create_table('automation_consent_authorizations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('field_id', sa.UUID(), nullable=False),
    sa.Column('field_key', sa.String(length=255), nullable=False),
    sa.Column('consent_text', sa.Text(), nullable=False),
    sa.Column('consent_text_hash', sa.String(length=64), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoke_reason', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("consent_text_hash ~ '^[0-9a-f]{64}$'", name='ck_automation_consent_authorizations_text_hash'),
    sa.ForeignKeyConstraint(['field_id', 'user_id'], ['automation_fields.id', 'automation_fields.user_id'], name='fk_automation_consent_authorizations_field', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id', 'user_id'], ['automation_sessions.id', 'automation_sessions.user_id'], name='fk_automation_consent_authorizations_session', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_automation_consent_authorizations_session', 'automation_consent_authorizations', ['session_id'], unique=False)
    op.create_index('ix_automation_consent_authorizations_user_id', 'automation_consent_authorizations', ['user_id'], unique=False)
    op.create_index('uq_automation_consent_authorizations_one_live', 'automation_consent_authorizations', ['field_id'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('automation_submission_authorizations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('final_review_id', sa.UUID(), nullable=False),
    sa.Column('reviewed_state_hash', sa.String(length=64), nullable=False),
    sa.Column('destination_host', sa.String(length=255), nullable=False),
    sa.Column('status', _enum('submission_authorization_status'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('invalidation_reason', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'CONSUMED') = (consumed_at IS NOT NULL)", name='ck_automation_submission_authorizations_consumed'),
    sa.CheckConstraint("(status = 'INVALIDATED') = (invalidated_at IS NOT NULL)", name='ck_automation_submission_authorizations_invalidated'),
    sa.CheckConstraint("reviewed_state_hash ~ '^[0-9a-f]{64}$'", name='ck_automation_submission_authorizations_hash'),
    sa.CheckConstraint('expires_at > created_at', name='ck_automation_submission_authorizations_expiry'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_automation_submission_authorizations_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['final_review_id', 'user_id'], ['automation_final_reviews.id', 'automation_final_reviews.user_id'], name='fk_automation_submission_authorizations_review', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id', 'user_id'], ['automation_sessions.id', 'automation_sessions.user_id'], name='fk_automation_submission_authorizations_session', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('final_review_id', name='uq_automation_submission_authorizations_review'),
    sa.UniqueConstraint('id', 'user_id', name='uq_automation_submission_authorizations_id_user')
    )
    op.create_index('ix_automation_submission_authorizations_session', 'automation_submission_authorizations', ['session_id'], unique=False)
    op.create_index('ix_automation_submission_authorizations_user_id', 'automation_submission_authorizations', ['user_id'], unique=False)
    op.create_index('uq_automation_submission_authorizations_one_open', 'automation_submission_authorizations', ['application_id'], unique=True, postgresql_where=sa.text("status = 'AUTHORIZED'"))
    op.create_table('automation_submission_attempts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('authorization_id', sa.UUID(), nullable=False),
    sa.Column('final_review_id', sa.UUID(), nullable=False),
    sa.Column('reviewed_state_hash', sa.String(length=64), nullable=False),
    sa.Column('destination_domain', sa.String(length=255), nullable=False),
    sa.Column('destination_url', sa.String(length=2000), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('submit_triggered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result', _enum('submission_result'), nullable=True),
    sa.Column('confirmation_reference', sa.String(length=80), nullable=True),
    sa.Column('confirmation_summary', sa.String(length=300), nullable=True),
    sa.Column('failure_code', sa.String(length=50), nullable=True),
    sa.Column('failure_reason', sa.String(length=300), nullable=True),
    sa.Column('user_verified_outcome', sa.String(length=20), nullable=True),
    sa.Column('user_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("result IS NULL OR result <> 'FAILED_BEFORE_SUBMIT' OR submit_triggered_at IS NULL", name='ck_automation_submission_attempts_failed_not_pressed'),
    sa.CheckConstraint("result IS NULL OR result = 'FAILED_BEFORE_SUBMIT' OR submit_triggered_at IS NOT NULL", name='ck_automation_submission_attempts_pressed'),
    sa.CheckConstraint("user_verified_outcome IS NULL OR (result = 'SUBMISSION_UNKNOWN' AND user_verified_outcome IN ('SUBMITTED', 'NOT_SUBMITTED') AND user_verified_at IS NOT NULL)", name='ck_automation_submission_attempts_verified'),
    sa.CheckConstraint('(result IS NULL) = (finished_at IS NULL)', name='ck_automation_submission_attempts_finished'),
    sa.ForeignKeyConstraint(['application_id', 'user_id'], ['applications.id', 'applications.user_id'], name='fk_automation_submission_attempts_application', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['authorization_id', 'user_id'], ['automation_submission_authorizations.id', 'automation_submission_authorizations.user_id'], name='fk_automation_submission_attempts_authorization', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id', 'user_id'], ['automation_sessions.id', 'automation_sessions.user_id'], name='fk_automation_submission_attempts_session', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('authorization_id', name='uq_automation_submission_attempts_authorization')
    )
    op.create_index('ix_automation_submission_attempts_application', 'automation_submission_attempts', ['application_id', 'started_at'], unique=False)
    op.create_index('ix_automation_submission_attempts_user_id', 'automation_submission_attempts', ['user_id'], unique=False)
    op.create_index('uq_automation_submission_attempts_one_running', 'automation_submission_attempts', ['application_id'], unique=True, postgresql_where=sa.text('finished_at IS NULL'))
    op.add_column('automation_fields', sa.Column('consent_text', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('automation_fields', 'consent_text')
    op.drop_index('uq_automation_submission_attempts_one_running', table_name='automation_submission_attempts', postgresql_where=sa.text('finished_at IS NULL'))
    op.drop_index('ix_automation_submission_attempts_user_id', table_name='automation_submission_attempts')
    op.drop_index('ix_automation_submission_attempts_application', table_name='automation_submission_attempts')
    op.drop_table('automation_submission_attempts')
    op.drop_index('uq_automation_submission_authorizations_one_open', table_name='automation_submission_authorizations', postgresql_where=sa.text("status = 'AUTHORIZED'"))
    op.drop_index('ix_automation_submission_authorizations_user_id', table_name='automation_submission_authorizations')
    op.drop_index('ix_automation_submission_authorizations_session', table_name='automation_submission_authorizations')
    op.drop_table('automation_submission_authorizations')
    op.drop_index('uq_automation_consent_authorizations_one_live', table_name='automation_consent_authorizations', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index('ix_automation_consent_authorizations_user_id', table_name='automation_consent_authorizations')
    op.drop_index('ix_automation_consent_authorizations_session', table_name='automation_consent_authorizations')
    op.drop_table('automation_consent_authorizations')
    op.drop_index('uq_automation_final_reviews_one_active', table_name='automation_final_reviews', postgresql_where=sa.text("status = 'ACTIVE'"))
    op.drop_index('ix_automation_final_reviews_user_id', table_name='automation_final_reviews')
    op.drop_index('ix_automation_final_reviews_application', table_name='automation_final_reviews')
    op.drop_table('automation_final_reviews')
    bind = op.get_bind()
    for name, values in ENUMS.items():
        sa.Enum(*values, name=name).drop(bind, checkfirst=True)
