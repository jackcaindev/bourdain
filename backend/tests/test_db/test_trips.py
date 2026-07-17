from uuid import uuid4

from app.db.trips import create_trip, get_trip_by_id, get_trip_by_session_id
from tests.test_db.helpers import DatabaseTestCase


class TripDatabaseTests(DatabaseTestCase):
    module_name = "trips"

    async def test_create_and_read_trip(self):
        session_id = f"session-{uuid4()}"
        trip = await create_trip(
            destination_raw="CDMX",
            destination_place_id="google-destination",
            destination_formatted="Mexico City, CDMX, Mexico",
            trip_length_days=4,
            activity_drivers=["markets"],
            food_selections=["street food"],
            time_blocks=["lunch", "dinner"],
            session_id=session_id,
        )

        self.assertEqual((await get_trip_by_id(trip.id)), trip)
        self.assertEqual((await get_trip_by_session_id(session_id)), trip)
        self.assertIsNone(await get_trip_by_id(uuid4()))
