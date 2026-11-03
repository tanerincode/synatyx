"""add_messages

Revision ID: f6c2d8e41a93
Revises: e2b9a4c7d615
Create Date: 2026-11-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f6c2d8e41a93'
down_revision: str | None = 'e2b9a4c7d615'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('messages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('sender_user_id', sa.String(), nullable=False),
    sa.Column('sender_session', sa.String(), nullable=True),
    sa.Column('sender_auth', sa.String(), nullable=True),
    sa.Column('recipient_user_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('parent_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['parent_id'], ['messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    # the brief's inbox query: unread for one recipient, newest first
    op.create_index('ix_messages_recipient_status_created', 'messages',
                    ['recipient_user_id', 'status', 'created_at'], unique=False)
    op.create_index('ix_messages_sender_created', 'messages',
                    ['sender_user_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_messages_sender_created', table_name='messages')
    op.drop_index('ix_messages_recipient_status_created', table_name='messages')
    op.drop_table('messages')
