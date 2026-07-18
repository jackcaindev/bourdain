"""Add activity and meal roles to itinerary slots.

Revision ID: 7c4e91a6b2d8
Revises: 5f3a8c1d2e90
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op


revision: str = "7c4e91a6b2d8"
down_revision: str | Sequence[str] | None = "5f3a8c1d2e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE itinerary_slots
            ADD COLUMN slot_role TEXT NOT NULL DEFAULT 'activity'
                CHECK (slot_role IN ('activity', 'meal'));
        ALTER TABLE itinerary_slots ALTER COLUMN slot_role DROP DEFAULT;
        ALTER TABLE itinerary_slots
            DROP CONSTRAINT itinerary_slots_itinerary_day_id_time_block_key;
        CREATE UNIQUE INDEX itinerary_slots_one_activity_per_block
            ON itinerary_slots (itinerary_day_id, time_block)
            WHERE slot_role = 'activity';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX itinerary_slots_one_activity_per_block;
        ALTER TABLE itinerary_slots
            ADD CONSTRAINT itinerary_slots_itinerary_day_id_time_block_key
            UNIQUE (itinerary_day_id, time_block);
        ALTER TABLE itinerary_slots DROP COLUMN slot_role;
        """
    )
