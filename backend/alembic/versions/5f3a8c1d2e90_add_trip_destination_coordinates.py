"""Add resolved destination coordinates to trips.

Revision ID: 5f3a8c1d2e90
Revises: 1d1c9fbd7a44
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op


revision: str = "5f3a8c1d2e90"
down_revision: str | Sequence[str] | None = "1d1c9fbd7a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trips "
        "ADD COLUMN destination_lat DOUBLE PRECISION NOT NULL, "
        "ADD COLUMN destination_lng DOUBLE PRECISION NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE trips "
        "DROP COLUMN destination_lat, "
        "DROP COLUMN destination_lng"
    )
