"""operational counters for the owner's health panel

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-25 17:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ops_events',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=48), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('last_note', sa.String(length=240), nullable=True),
    sa.Column('last_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('day', 'kind', 'name')
    )


def downgrade() -> None:
    op.drop_table('ops_events')
