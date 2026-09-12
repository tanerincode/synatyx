"""add_oauth_clients_table

Revision ID: c5a7e31b9d48
Revises: b8f2d4a91c36
Create Date: 2026-09-12 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c5a7e31b9d48'
down_revision: str | None = 'b8f2d4a91c36'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('oauth_clients',
    sa.Column('client_id', sa.String(), nullable=False),
    sa.Column('client_name', sa.String(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    # NULL until the first token is issued; unused rows are pruned after 24h
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('client_id')
    )


def downgrade() -> None:
    op.drop_table('oauth_clients')
