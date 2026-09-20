"""add immutable chat mode

Revision ID: e7a9c2d4f6b8
Revises: d4c1a8e37b62
Create Date: 2026-09-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a9c2d4f6b8'
down_revision: str | None = 'd4c1a8e37b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('chat', sa.Column('mode', sa.String(), nullable=False, server_default='chat'))
    op.create_index('user_id_mode_updated_at_idx', 'chat', ['user_id', 'mode', sa.text('updated_at DESC')])


def downgrade() -> None:
    op.drop_index('user_id_mode_updated_at_idx', table_name='chat')
    op.drop_column('chat', 'mode')
