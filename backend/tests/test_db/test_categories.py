from app.db.categories import create_category, get_categories_by_trip_id
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
            estimated_duration_minutes=90,
            neighborhood_scope="Centro",
            day_number=1,
            time_block="breakfast",
        )

        self.assertEqual(await get_categories_by_trip_id(trip_id), [category])
        await self.connection.execute("DELETE FROM trips WHERE id = $1", trip_id)
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT count(*) FROM categories WHERE id = $1", category.id
            ),
            0,
        )
