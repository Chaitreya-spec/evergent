"""stored objects, for STORAGE_BACKEND=database

Revision ID: b5d1c0a7e3f9
Revises: 7ca0da14e9bd
Create Date: 2026-10-22 18:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b5d1c0a7e3f9'
down_revision: str | None = '7ca0da14e9bd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('stored_objects',
    sa.Column('key', sa.String(length=300), nullable=False),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('stored_objects')
