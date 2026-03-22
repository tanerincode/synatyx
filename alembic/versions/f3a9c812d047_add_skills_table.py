"""add_skills_table

Revision ID: f3a9c812d047
Revises: 24bff5aac84e
Create Date: 2026-03-22 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f3a9c812d047'
down_revision: Union[str, None] = '24bff5aac84e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'skills',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('frontmatter', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('project', sa.String(), nullable=True),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.UniqueConstraint('slug'),
    )
    op.create_index(op.f('ix_skills_user_id'), 'skills', ['user_id'], unique=False)
    op.create_index(op.f('ix_skills_project'), 'skills', ['project'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_skills_project'), table_name='skills')
    op.drop_index(op.f('ix_skills_user_id'), table_name='skills')
    op.drop_table('skills')

