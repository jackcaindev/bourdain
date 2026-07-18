import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services import places
from app.services.places import PlacesQueryError


def _raw_place(identifier: str, name: str, address: str) -> dict:
    return {
        "id": identifier,
        "displayName": {"text": name, "languageCode": "en"},
        "formattedAddress": address,
        "location": {"latitude": 41.1579, "longitude": -8.6291},
        "types": ["locality", "political"],
    }


class PlacesTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncMock(spec=httpx.AsyncClient)
        self.settings = SimpleNamespace(
            google_places_api_key=MagicMock(
                get_secret_value=MagicMock(return_value="test-key")
            )
        )
        self.client_patcher = patch(
            "app.services.places.httpx.AsyncClient", return_value=self.client
        )
        self.settings_patcher = patch(
            "app.services.places.get_settings", return_value=self.settings
        )
        self.client_patcher.start()
        self.settings_patcher.start()
        places._shared_client = None

    async def asyncTearDown(self):
        places._shared_client = None
        self.settings_patcher.stop()
        self.client_patcher.stop()

    async def test_resolve_city_returns_single_match(self):
        self.client.post.return_value = httpx.Response(
            200,
            json={"places": [_raw_place("porto-id", "Porto", "Porto, Portugal")]},
        )

        resolution = await places.resolve_city("Porto")

        self.assertEqual(resolution.status, "resolved")
        self.assertIsNotNone(resolution.match)
        assert resolution.match is not None
        self.assertEqual(resolution.match.google_place_id, "porto-id")
        self.assertEqual(resolution.match.name, "Porto")
        self.assertEqual(resolution.match.formatted_address, "Porto, Portugal")

    async def test_resolve_city_returns_up_to_five_ambiguous_candidates(self):
        self.client.post.return_value = httpx.Response(
            200,
            json={
                "places": [
                    _raw_place(f"springfield-{index}", "Springfield", f"Area {index}")
                    for index in range(6)
                ]
            },
        )

        resolution = await places.resolve_city("Springfield")

        self.assertEqual(resolution.status, "ambiguous")
        self.assertIsNone(resolution.match)
        self.assertEqual(len(resolution.candidates), 5)
        self.assertEqual(resolution.candidates[0].google_place_id, "springfield-0")

    async def test_resolve_city_raises_when_no_matches_exist(self):
        self.client.post.return_value = httpx.Response(200, json={})

        with self.assertRaises(PlacesQueryError):
            await places.resolve_city("Definitely Not A City")

    async def test_city_search_uses_only_pro_tier_field_mask(self):
        self.client.post.return_value = httpx.Response(
            200,
            json={"places": [_raw_place("porto-id", "Porto", "Porto, Portugal")]},
        )

        await places.resolve_city("Porto")

        _, kwargs = self.client.post.await_args
        field_mask = kwargs["headers"]["X-Goog-FieldMask"]
        self.assertEqual(field_mask, places.CITY_FIELD_MASK)
        for forbidden_field in (
            "rating",
            "pricelevel",
            "openinghours",
            "currentopeninghours",
            "regularopeninghours",
        ):
            self.assertNotIn(forbidden_field, field_mask.lower())

    async def test_verify_venue_uses_scoped_query_bias_and_top_match(self):
        self.client.post.return_value = httpx.Response(
            200,
            json={
                "places": [
                    _raw_place("cafe-id", "Cafe Local", "Cafe Local, Centro, Porto"),
                    _raw_place("other-id", "Cafe Local", "Another address"),
                ]
            },
        )

        match = await places.verify_venue(
            "Cafe Local",
            neighborhood_scope="Centro",
            city_name="Porto",
            location_bias=(41.1579, -8.6291),
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.google_place_id, "cafe-id")
        _, kwargs = self.client.post.await_args
        self.assertEqual(kwargs["json"]["textQuery"], "Cafe Local, Centro, Porto")
        self.assertEqual(
            kwargs["json"]["locationBias"]["circle"]["center"],
            {"latitude": 41.1579, "longitude": -8.6291},
        )
        self.assertEqual(
            kwargs["headers"]["X-Goog-FieldMask"], places.VENUE_FIELD_MASK
        )

    async def test_verify_venue_rejects_low_name_similarity(self):
        self.client.post.return_value = httpx.Response(
            200,
            json={
                "places": [
                    _raw_place("wrong-id", "Unrelated Museum", "Centro, Porto")
                ]
            },
        )

        match = await places.verify_venue(
            "Cafe Local",
            neighborhood_scope="Centro",
            city_name="Porto",
            location_bias=(41.1579, -8.6291),
        )

        self.assertIsNone(match)

    async def test_search_rejects_non_pro_fields_before_request(self):
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            await places.search_text("Porto", field_mask="places.id,places.rating")

        self.client.post.assert_not_awaited()

    async def test_shared_client_is_created_once_for_concurrent_callers(self):
        clients = await asyncio.gather(
            *(places.get_shared_client() for _ in range(20))
        )

        self.assertTrue(all(client is self.client for client in clients))
        self.assertEqual(places.httpx.AsyncClient.call_count, 1)
