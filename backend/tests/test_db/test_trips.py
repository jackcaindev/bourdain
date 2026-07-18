from uuid import uuid4

from app.db.trips import (
    TripError,
    create_trip,
    get_trip_by_id,
    get_trip_by_session_id,
    update_trip_status,
)
from tests.test_db.helpers import DatabaseTestCase


class TripDatabaseTests(DatabaseTestCase):
    module_name = "trips"

    async def test_create_and_read_trip(self):
        session_id = f"session-{uuid4()}"
        trip = await create_trip(
            destination_raw="CDMX",
            destination_place_id="google-destination",
            destination_formatted="Mexico City, CDMX, Mexico",
            destination_lat=19.4326,
            destination_lng=-99.1332,
            trip_length_days=4,
            activity_drivers=["markets"],
            food_selections=["street food"],
            time_blocks=["lunch", "dinner"],
            session_id=session_id,
        )

        self.assertEqual(trip.destination_lat, 19.4326)
        self.assertEqual(trip.destination_lng, -99.1332)
        self.assertEqual((await get_trip_by_id(trip.id)), trip)
        self.assertEqual((await get_trip_by_session_id(session_id)), trip)
        self.assertIsNone(await get_trip_by_id(uuid4()))

        updated = await update_trip_status(trip.id, "researching")
        self.assertEqual(updated.status, "researching")
        self.assertGreaterEqual(updated.updated_at, trip.updated_at)

        with self.assertRaisesRegex(TripError, "was not found"):
            await update_trip_status(uuid4(), "reviewing")
