"""wake-up slots booked with the outside scheduler

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-25 13:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('wake_slots',
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('booked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('at')
    )


def downgrade() -> None:
    op.drop_table('wake_slots')
