from uuid import uuid4

import asyncpg

from app.db.places import get_or_create_place
from tests.test_db.helpers import DatabaseTestCase


class PlaceDatabaseTests(DatabaseTestCase):
    module_name = "places"

    async def test_get_or_create_deduplicates_google_place_id(self):
        google_place_id = f"place-{uuid4()}"
        first = await get_or_create_place(
            google_place_id=google_place_id,
            name="Original Name",
            formatted_address="1 Test Street",
            lat=40.1,
            lng=-73.9,
            google_types=["restaurant"],
        )
        second = await get_or_create_place(
            google_place_id=google_place_id,
            name="Ignored Replacement",
            formatted_address="2 Other Street",
            lat=1.0,
            lng=2.0,
        )

        self.assertEqual(first, second)
        count = await self.connection.fetchval(
            "SELECT count(*) FROM places WHERE google_place_id = $1", google_place_id
        )
        self.assertEqual(count, 1)

    async def test_google_place_id_unique_constraint_rejects_duplicate_insert(self):
        google_place_id = f"place-{uuid4()}"
        await self.connection.execute(
            """
            INSERT INTO places (
                google_place_id, name, formatted_address, lat, lng
            ) VALUES ($1, 'First', 'Address', 0, 0)
            """,
            google_place_id,
        )

        with self.assertRaises(asyncpg.UniqueViolationError):
            async with self.connection.transaction():
                await self.connection.execute(
                    """
                    INSERT INTO places (
                        google_place_id, name, formatted_address, lat, lng
                    ) VALUES ($1, 'Second', 'Address', 0, 0)
                    """,
                    google_place_id,
                )
