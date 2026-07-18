"""Add deterministic eligible time blocks to categories.

Revision ID: 1d1c9fbd7a44
Revises: 96239a912e74
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op


revision: str = "1d1c9fbd7a44"
down_revision: str | Sequence[str] | None = "96239a912e74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE categories "
        "ADD COLUMN eligible_blocks TEXT[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE categories DROP COLUMN eligible_blocks")
