"""v0.13 security & privacy: auth sessions, consent records, AI processing preference

Revision ID: e9c4b2a7d1f3
Revises: d6a3f1b8c2e5
Create Date: 2026-10-14 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e9c4b2a7d1f3'
down_revision: Union[str, None] = 'd6a3f1b8c2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Notes:
#
# - auth_sessions: one row per sign-in; access tokens issued before this revision carry no
#   session id and are therefore no longer accepted -- users sign in again once (tokens
#   lived at most ACCESS_TOKEN_EXPIRE_MINUTES anyway). No existing data changes.
# - users.external_ai_processing_allowed: existing users keep today's behaviour (true),
#   with no consent record: none was ever asked for. The user can switch it off in
#   Settings > Privacy.
# - consent_records: append-only decisions; empty at first.
# - downgrade() drops all three. Sessions and recorded decisions are lost (nothing in
#   V0.12 could use them).


def upgrade() -> None:
    op.create_table(
        'auth_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'], unique=False)

    op.create_table(
        'consent_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('policy_version', sa.String(length=64), nullable=False),
        sa.Column('granted', sa.Boolean(), nullable=False),
        sa.Column(
            'decided_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('external_ai_processing')", name='ck_consent_records_category'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_consent_records_user_category', 'consent_records',
        ['user_id', 'category', 'decided_at'], unique=False,
    )

    op.add_column(
        'users',
        sa.Column(
            'external_ai_processing_allowed', sa.Boolean(), server_default=sa.true(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'external_ai_processing_allowed')
    op.drop_index('ix_consent_records_user_category', table_name='consent_records')
    op.drop_table('consent_records')
    op.drop_index('ix_auth_sessions_user_id', table_name='auth_sessions')
    op.drop_table('auth_sessions')
