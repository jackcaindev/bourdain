from app.db.selections import (
    create_category_selection,
    create_venue_selection,
    get_venue_selections_by_trip_id,
)
from tests.test_db.helpers import DatabaseTestCase


class SelectionDatabaseTests(DatabaseTestCase):
    module_name = "selections"

    async def test_create_category_and_venue_selections_and_read_joined_details(self):
        trip_id = await self.insert_trip()
        category_id = await self.insert_category(trip_id)
        place_id = await self.insert_place()
        run_id = await self.insert_research_run(trip_id, category_id)
        recommendation_id = await self.insert_recommendation(
            trip_id, category_id, run_id, place_id
        )

        category_selection = await create_category_selection(
            trip_id=trip_id, category_id=category_id
        )
        venue_selection = await create_venue_selection(
            trip_id=trip_id,
            recommendation_id=recommendation_id,
            day_number=2,
            time_block="lunch",
        )

        self.assertEqual(category_selection.category_id, category_id)
        selections = await get_venue_selections_by_trip_id(trip_id)
        self.assertEqual(len(selections), 1)
        detail = selections[0]
        self.assertEqual(detail.id, venue_selection.id)
        self.assertEqual(detail.place_id, place_id)
        self.assertEqual(detail.neighborhood_scope, "Old Town")
        self.assertEqual(detail.place_name, "Corner Cafe")
        self.assertEqual((detail.lat, detail.lng), (40.1, -73.9))
