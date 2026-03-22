"""add_gc_log_table

Revision ID: d7e1f3b2c804
Revises: f3a9c812d047
Create Date: 2026-03-22 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e1f3b2c804'
down_revision: Union[str, None] = 'f3a9c812d047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'gc_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.String(), nullable=False),
        sa.Column('item_id', sa.String(), nullable=False),
        sa.Column('collection', sa.String(), nullable=False),
        sa.Column('memory_layer', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),  # "deprecated" | "deleted"
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_gc_log_run_id'), 'gc_log', ['run_id'], unique=False)
    op.create_index(op.f('ix_gc_log_action'), 'gc_log', ['action'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_gc_log_action'), table_name='gc_log')
    op.drop_index(op.f('ix_gc_log_run_id'), table_name='gc_log')
    op.drop_table('gc_log')

