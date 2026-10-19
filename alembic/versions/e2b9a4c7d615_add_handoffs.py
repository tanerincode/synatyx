"""add_handoffs

Revision ID: e2b9a4c7d615
Revises: c5a7e31b9d48
Create Date: 2026-10-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2b9a4c7d615'
down_revision: str | None = 'c5a7e31b9d48'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('handoffs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('project', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('assignee', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('parent_id', sa.String(), nullable=True),
    sa.Column('task_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.PrimaryKeyConstraint('id'),
    # unique per user and project, not global
    sa.UniqueConstraint('user_id', 'project', 'slug', name='uq_handoffs_user_project_slug')
    )
    op.create_index('ix_handoffs_user_assignee_status', 'handoffs',
                    ['user_id', 'assignee', 'status'], unique=False)

    op.create_table('handoff_versions',
    sa.Column('handoff_id', sa.String(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('author', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.ForeignKeyConstraint(['handoff_id'], ['handoffs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('handoff_id', 'version')
    )

    op.create_table('handoff_reports',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('handoff_id', sa.String(), nullable=False),
    sa.Column('agent', sa.String(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status_after', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.ForeignKeyConstraint(['handoff_id'], ['handoffs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_handoff_reports_handoff_created', 'handoff_reports',
                    ['handoff_id', 'created_at'], unique=False)

    op.add_column('tasks', sa.Column('assignee', sa.String(), nullable=True))
    op.create_index(op.f('ix_tasks_assignee'), 'tasks', ['assignee'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tasks_assignee'), table_name='tasks')
    op.drop_column('tasks', 'assignee')
    op.drop_index('ix_handoff_reports_handoff_created', table_name='handoff_reports')
    op.drop_table('handoff_reports')
    op.drop_table('handoff_versions')
    op.drop_index('ix_handoffs_user_assignee_status', table_name='handoffs')
    op.drop_table('handoffs')
