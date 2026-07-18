from app.db.itineraries import (
    create_itinerary,
    create_itinerary_day,
    create_meal_slot,
    upsert_activity_slot,
)
from tests.test_db.helpers import DatabaseTestCase


class ItinerarySlotDatabaseTests(DatabaseTestCase):
    module_name = "itineraries"

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.trip_id = await self.insert_trip()
        self.category_id = await self.insert_category(self.trip_id)
        self.place_id = await self.insert_place()
        self.run_id = await self.insert_research_run(
            self.trip_id, self.category_id
        )
        itinerary = await create_itinerary(trip_id=self.trip_id)
        self.day = await create_itinerary_day(
            itinerary_id=itinerary.id, day_number=1
        )

    async def _recommendation_id(self):
        return await self.insert_recommendation(
            self.trip_id,
            self.category_id,
            self.run_id,
            self.place_id,
        )

    async def test_activity_upsert_updates_partial_unique_index_row(self):
        first_recommendation_id = await self._recommendation_id()
        second_recommendation_id = await self._recommendation_id()

        first = await upsert_activity_slot(
            itinerary_day_id=self.day.id,
            time_block="morning",
            recommendation_id=first_recommendation_id,
        )
        updated = await upsert_activity_slot(
            itinerary_day_id=self.day.id,
            time_block="morning",
            recommendation_id=second_recommendation_id,
        )

        self.assertEqual(first.id, updated.id)
        self.assertEqual(updated.slot_role, "activity")
        self.assertEqual(updated.recommendation_id, second_recommendation_id)
        self.assertEqual(
            await self.connection.fetchval(
                """
                SELECT count(*) FROM itinerary_slots
                WHERE itinerary_day_id = $1
                  AND time_block = 'morning'
                  AND slot_role = 'activity'
                """,
                self.day.id,
            ),
            1,
        )

    async def test_meal_inserts_allow_multiple_rows_in_same_block(self):
        first_recommendation_id = await self._recommendation_id()
        second_recommendation_id = await self._recommendation_id()

        first = await create_meal_slot(
            itinerary_day_id=self.day.id,
            time_block="morning",
            recommendation_id=first_recommendation_id,
        )
        second = await create_meal_slot(
            itinerary_day_id=self.day.id,
            time_block="morning",
            recommendation_id=second_recommendation_id,
        )

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.slot_role, "meal")
        self.assertEqual(second.slot_role, "meal")
        self.assertEqual(
            await self.connection.fetchval(
                """
                SELECT count(*) FROM itinerary_slots
                WHERE itinerary_day_id = $1
                  AND time_block = 'morning'
                  AND slot_role = 'meal'
                """,
                self.day.id,
            ),
            2,
        )
