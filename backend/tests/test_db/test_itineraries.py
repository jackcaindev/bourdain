from app.db.itineraries import (
    confirm_itinerary_day,
    create_itinerary,
    create_itinerary_day,
    upsert_itinerary_slot,
)
from tests.test_db.helpers import DatabaseTestCase


class ItineraryDatabaseTests(DatabaseTestCase):
    module_name = "itineraries"

    async def test_create_day_upsert_slot_and_confirm_day(self):
        trip_id = await self.insert_trip()
        category_id = await self.insert_category(trip_id)
        place_id = await self.insert_place()
        run_id = await self.insert_research_run(trip_id, category_id)
        recommendation_id = await self.insert_recommendation(
            trip_id, category_id, run_id, place_id
        )

        itinerary = await create_itinerary(trip_id=trip_id)
        day = await create_itinerary_day(
            itinerary_id=itinerary.id, day_number=1
        )
        empty_slot = await upsert_itinerary_slot(
            itinerary_day_id=day.id, time_block="dinner"
        )
        filled_slot = await upsert_itinerary_slot(
            itinerary_day_id=day.id,
            time_block="dinner",
            recommendation_id=recommendation_id,
        )
        confirmed_day = await confirm_itinerary_day(day.id)

        self.assertEqual(empty_slot.id, filled_slot.id)
        self.assertEqual(filled_slot.recommendation_id, recommendation_id)
        self.assertEqual(confirmed_day.status, "confirmed")
