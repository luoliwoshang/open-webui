"""Add AgentAPI profiles and chat mode.

Revision ID: e6f7a8b9c0d1
Revises: d4c1a8e37b62
Create Date: 2026-09-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables

revision: str = 'e6f7a8b9c0d1'
down_revision: str | None = 'd4c1a8e37b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if 'agent_profile' not in existing_tables:
        op.create_table(
            'agent_profile',
            sa.Column('id', sa.Text(), nullable=False, primary_key=True),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('agent_id', sa.Text(), nullable=False),
            sa.Column('environment_id', sa.Text(), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_by', sa.Text(), nullable=False),
            sa.Column('meta', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('idx_agent_profile_enabled', 'agent_profile', ['enabled'])

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    chat_columns = {column['name'] for column in inspector.get_columns('chat')}
    if 'mode' not in chat_columns:
        op.add_column('chat', sa.Column('mode', sa.Text(), nullable=False, server_default='chat'))
        op.create_index('idx_chat_user_mode_updated', 'chat', ['user_id', 'mode', 'updated_at'])


def downgrade() -> None:
    existing_tables = set(get_existing_tables())
    if 'chat' in existing_tables:
        inspector = sa.inspect(op.get_bind())
        chat_columns = {column['name'] for column in inspector.get_columns('chat')}
        if 'mode' in chat_columns:
            op.drop_index('idx_chat_user_mode_updated', table_name='chat')
            op.drop_column('chat', 'mode')

    if 'agent_profile' in existing_tables:
        op.drop_index('idx_agent_profile_enabled', table_name='agent_profile')
        op.drop_table('agent_profile')
