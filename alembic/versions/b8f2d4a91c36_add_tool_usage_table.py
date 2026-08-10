"""add_tool_usage_table

Revision ID: b8f2d4a91c36
Revises: a1c4e9f27b53
Create Date: 2026-08-10 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8f2d4a91c36'
down_revision: str | None = 'a1c4e9f27b53'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tool_usage',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('project', sa.String(), nullable=True),
    sa.Column('tool', sa.String(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('embedding_tokens', sa.Integer(), nullable=False),
    sa.Column('error', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tool_usage_user_created', 'tool_usage',
                    ['user_id', 'created_at'], unique=False)
    op.create_index('ix_tool_usage_project_created', 'tool_usage',
                    ['project', 'created_at'], unique=False)
    op.create_index(op.f('ix_tool_usage_tool'), 'tool_usage', ['tool'], unique=False)
    op.create_index(op.f('ix_tool_usage_created_at'), 'tool_usage', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tool_usage_created_at'), table_name='tool_usage')
    op.drop_index(op.f('ix_tool_usage_tool'), table_name='tool_usage')
    op.drop_index('ix_tool_usage_project_created', table_name='tool_usage')
    op.drop_index('ix_tool_usage_user_created', table_name='tool_usage')
    op.drop_table('tool_usage')
