from app.db.itineraries import (
    all_days_confirmed,
    confirm_itinerary_day,
    create_itinerary,
    create_itinerary_day,
    create_meal_slot,
    get_itinerary_with_details,
    get_slot_trip_and_category,
    upsert_activity_slot,
)
from tests.test_db.helpers import DatabaseTestCase


class ItineraryDatabaseTests(DatabaseTestCase):
    module_name = "itineraries"

    async def test_create_itinerary_day_and_confirm_day(self):
        trip_id = await self.insert_trip()
        itinerary = await create_itinerary(trip_id=trip_id)
        day = await create_itinerary_day(
            itinerary_id=itinerary.id, day_number=1
        )
        confirmed_day = await confirm_itinerary_day(day.id)

        self.assertEqual(confirmed_day.status, "confirmed")

    async def test_get_itinerary_groups_activity_and_two_meals_with_evidence(self):
        trip_id = await self.insert_trip()
        category_id = await self.insert_category(trip_id)
        run_id = await self.insert_research_run(trip_id, category_id)
        place_ids = [await self.insert_place() for _ in range(3)]
        recommendation_ids = [
            await self.insert_recommendation(
                trip_id, category_id, run_id, place_id
            )
            for place_id in place_ids
        ]
        for index, place_id in enumerate(place_ids, start=1):
            await self.connection.execute(
                "UPDATE places SET name = $2, google_types = $3 WHERE id = $1",
                place_id,
                f"Place {index}",
                ["restaurant"],
            )
            await self.connection.execute(
                """
                INSERT INTO evidence (
                    place_id, research_run_id, source_type, raw_content
                ) VALUES
                    ($1, $2, 'vector_store', $3),
                    ($1, $2, 'places_api', $4)
                """,
                place_id,
                run_id,
                f"Editorial evidence {index}",
                f"Places payload {index}",
            )

        itinerary = await create_itinerary(trip_id=trip_id)
        day = await create_itinerary_day(itinerary_id=itinerary.id, day_number=1)
        activity_slot = await upsert_activity_slot(
            itinerary_day_id=day.id,
            time_block="morning",
            recommendation_id=recommendation_ids[0],
        )
        first_meal_slot = await create_meal_slot(
            itinerary_day_id=day.id,
            time_block="morning",
            recommendation_id=recommendation_ids[1],
        )
        second_meal_slot = await create_meal_slot(
            itinerary_day_id=day.id,
            time_block="morning",
            recommendation_id=recommendation_ids[2],
        )

        result = await get_itinerary_with_details(trip_id)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.days[0].slots[0].activity.name, "Place 1")
        self.assertEqual(
            result.days[0].slots[0].activity.slot_id,
            activity_slot.id,
        )
        self.assertCountEqual(
            [meal.name for meal in result.days[0].slots[0].meals],
            ["Place 2", "Place 3"],
        )
        meal_slot_ids = {
            meal.slot_id for meal in result.days[0].slots[0].meals
        }
        self.assertEqual(
            meal_slot_ids,
            {first_meal_slot.id, second_meal_slot.id},
        )
        self.assertEqual(len(meal_slot_ids), 2)
        for meal_slot_id in meal_slot_ids:
            self.assertEqual(
                await get_slot_trip_and_category(meal_slot_id),
                (trip_id, category_id),
            )
        self.assertEqual(
            result.days[0].slots[0].activity.description,
            "Editorial evidence 1",
        )
        self.assertNotIn(
            "Places payload",
            result.days[0].slots[0].activity.description,
        )

    async def test_get_slot_trip_and_category(self):
        trip_id = await self.insert_trip()
        category_id = await self.insert_category(trip_id)
        place_id = await self.insert_place()
        run_id = await self.insert_research_run(trip_id, category_id)
        recommendation_id = await self.insert_recommendation(
            trip_id, category_id, run_id, place_id
        )
        itinerary = await create_itinerary(trip_id=trip_id)
        day = await create_itinerary_day(itinerary_id=itinerary.id, day_number=1)
        slot = await upsert_activity_slot(
            itinerary_day_id=day.id,
            time_block="afternoon",
            recommendation_id=recommendation_id,
        )

        self.assertEqual(
            await get_slot_trip_and_category(slot.id),
            (trip_id, category_id),
        )

    async def test_all_days_confirmed_changes_after_last_draft_day(self):
        trip_id = await self.insert_trip()
        itinerary = await create_itinerary(trip_id=trip_id)
        await create_itinerary_day(
            itinerary_id=itinerary.id, day_number=1, status="confirmed"
        )
        second = await create_itinerary_day(
            itinerary_id=itinerary.id, day_number=2
        )

        self.assertFalse(await all_days_confirmed(itinerary.id))
        await confirm_itinerary_day(second.id)
        self.assertTrue(await all_days_confirmed(itinerary.id))
