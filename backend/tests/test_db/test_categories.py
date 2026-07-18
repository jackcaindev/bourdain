from app.db.categories import (
    create_category,
    get_categories_by_trip_id,
    mark_categories_selected,
)
from tests.test_db.helpers import DatabaseTestCase


class CategoryDatabaseTests(DatabaseTestCase):
    module_name = "categories"

    async def test_create_and_read_categories_and_trip_delete_cascades(self):
        trip_id = await self.insert_trip()
        category = await create_category(
            trip_id=trip_id,
            name="Market breakfast",
            type="food",
            source_drivers=["markets"],
            eligible_blocks=["morning", "afternoon"],
            estimated_duration_minutes=90,
            neighborhood_scope="Centro",
            day_number=1,
            time_block="breakfast",
        )

        self.assertEqual(category.eligible_blocks, ["morning", "afternoon"])
        self.assertEqual(await get_categories_by_trip_id(trip_id), [category])
        await self.connection.execute("DELETE FROM trips WHERE id = $1", trip_id)
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT count(*) FROM categories WHERE id = $1", category.id
            ),
            0,
        )

    async def test_mark_categories_selected_updates_only_requested_trip_subset(self):
        trip_id = await self.insert_trip()
        selected_id = await self.insert_category(trip_id)
        unselected_id = await self.connection.fetchval(
            """
            INSERT INTO categories (
                trip_id, name, type, estimated_duration_minutes,
                neighborhood_scope, status
            )
            VALUES ($1, 'Unselected dinner', 'food', 90, 'Old Town', 'candidate')
            RETURNING id
            """,
            trip_id,
        )
        other_trip_id = await self.insert_trip()
        other_trip_category_id = await self.insert_category(other_trip_id)

        await mark_categories_selected(
            trip_id=trip_id, category_ids=[selected_id, other_trip_category_id]
        )

        rows = await self.connection.fetch(
            "SELECT id, status FROM categories WHERE id = ANY($1::uuid[])",
            [selected_id, unselected_id, other_trip_category_id],
        )
        statuses = {row["id"]: row["status"] for row in rows}
        self.assertEqual(statuses[selected_id], "selected")
        self.assertEqual(statuses[unselected_id], "candidate")
        self.assertEqual(statuses[other_trip_category_id], "candidate")
