from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.graph.city_profile_node import (
    ACTIVITY_BLOCK_AFFINITY,
    MEAL_BLOCK_AFFINITY,
    _category_tool_schema,
    city_profile_node,
)


def _trip(**overrides):
    values = {
        "id": uuid4(),
        "destination_formatted": "Oaxaca, Mexico",
        "destination_lat": 17.0732,
        "destination_lng": -96.7266,
        "trip_length_days": 4,
        "activity_drivers": ["Nightlife", "Arts & Music"],
        "food_selections": ["Breakfast", "Coffee"],
        "time_blocks": ["morning", "afternoon", "night"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _persisted_category(**kwargs):
    return SimpleNamespace(
        id=uuid4(),
        name=kwargs["name"],
        type=kwargs["type"],
        source_drivers=kwargs["source_drivers"],
        eligible_blocks=kwargs["eligible_blocks"],
        estimated_duration_minutes=kwargs["estimated_duration_minutes"],
        neighborhood_scope=kwargs["neighborhood_scope"],
        status=kwargs["status"],
    )


class CategoryDerivationSchemaTests(TestCase):
    def test_tool_schema_requires_two_to_three_categories_without_blocks(self):
        categories_schema = _category_tool_schema()["input_schema"]["properties"][
            "categories"
        ]

        self.assertEqual(categories_schema["minItems"], 2)
        self.assertEqual(categories_schema["maxItems"], 3)
        item_schema = categories_schema["items"]
        self.assertEqual(
            item_schema["required"],
            ["name", "estimated_duration_minutes", "neighborhood_scope"],
        )
        self.assertNotIn("eligible_blocks", item_schema["properties"])


class CityProfileNodeTests(IsolatedAsyncioTestCase):
    async def test_calls_llm_once_per_selected_driver_and_meal(self):
        trip = _trip()
        tool_result = {
            "categories": [
                {
                    "name": "First lane",
                    "estimated_duration_minutes": 90,
                    "neighborhood_scope": "Centro",
                    "eligible_blocks": ["night"],
                },
                {
                    "name": "Second lane",
                    "estimated_duration_minutes": 60,
                    "neighborhood_scope": "Jalatlaco",
                },
            ]
        }

        with (
            patch(
                "app.graph.city_profile_node.get_trip_by_session_id",
                new=AsyncMock(return_value=trip),
            ) as get_trip,
            patch(
                "app.graph.city_profile_node.call_forced_tool",
                return_value=tool_result,
            ) as llm_call,
            patch(
                "app.graph.city_profile_node.create_category",
                new=AsyncMock(side_effect=lambda **kwargs: _persisted_category(**kwargs)),
            ) as create_category,
        ):
            result = await city_profile_node(
                {"destination": "ignored", "trip_length_days": 4},
                {"configurable": {"thread_id": "trip-session"}},
            )

        get_trip.assert_awaited_once_with("trip-session")
        self.assertEqual(llm_call.call_count, 4)
        prompts = [call.kwargs["user_prompt"] for call in llm_call.call_args_list]
        for selection in [*trip.activity_drivers, *trip.food_selections]:
            self.assertEqual(sum(selection in prompt for prompt in prompts), 1)
        self.assertEqual(create_category.await_count, 8)
        self.assertEqual(len(result["categories"]), 8)
        self.assertEqual(result["trip_id"], trip.id)
        self.assertEqual(result["city_slug"], "oaxaca-mexico")
        self.assertEqual(result["destination_lat"], trip.destination_lat)
        self.assertEqual(result["destination_lng"], trip.destination_lng)
        self.assertEqual(result["time_blocks"], trip.time_blocks)

    async def test_eligible_blocks_come_only_from_static_affinity(self):
        trip = _trip(
            activity_drivers=["Nightlife"],
            food_selections=["Coffee"],
        )
        tool_result = {
            "categories": [
                {
                    "name": "A lane",
                    "estimated_duration_minutes": 45,
                    "neighborhood_scope": "Anywhere local",
                    "eligible_blocks": ["morning", "afternoon", "night"],
                },
                {
                    "name": "Another lane",
                    "estimated_duration_minutes": 75,
                    "neighborhood_scope": "Old town",
                },
            ]
        }

        with (
            patch(
                "app.graph.city_profile_node.get_trip_by_session_id",
                new=AsyncMock(return_value=trip),
            ),
            patch(
                "app.graph.city_profile_node.call_forced_tool",
                return_value=tool_result,
            ),
            patch(
                "app.graph.city_profile_node.create_category",
                new=AsyncMock(side_effect=lambda **kwargs: _persisted_category(**kwargs)),
            ) as create_category,
        ):
            result = await city_profile_node(
                {"destination": "ignored", "trip_length_days": 4},
                {"configurable": {"thread_id": "trip-session"}},
            )

        persisted_by_driver: dict[str, list[list[str]]] = {}
        for call in create_category.await_args_list:
            driver = call.kwargs["source_drivers"][0]
            persisted_by_driver.setdefault(driver, []).append(
                call.kwargs["eligible_blocks"]
            )
        self.assertEqual(
            persisted_by_driver["Nightlife"],
            [ACTIVITY_BLOCK_AFFINITY["Nightlife"]] * 2,
        )
        self.assertEqual(
            persisted_by_driver["Coffee"],
            [MEAL_BLOCK_AFFINITY["Coffee"]] * 2,
        )
        self.assertEqual(
            {tuple(category.eligible_blocks) for category in result["categories"]},
            {("night",), ("morning", "afternoon")},
        )

    async def test_malformed_driver_result_raises_before_persistence(self):
        trip = _trip(activity_drivers=["Nightlife"], food_selections=[])
        with (
            patch(
                "app.graph.city_profile_node.get_trip_by_session_id",
                new=AsyncMock(return_value=trip),
            ),
            patch(
                "app.graph.city_profile_node.call_forced_tool",
                return_value={
                    "categories": [
                        {
                            "name": "Only one",
                            "estimated_duration_minutes": 60,
                            "neighborhood_scope": "Centro",
                        }
                    ]
                },
            ),
            patch(
                "app.graph.city_profile_node.create_category",
                new_callable=AsyncMock,
            ) as create_category,
        ):
            with self.assertRaises(ValidationError):
                await city_profile_node(
                    {"destination": "ignored", "trip_length_days": 4},
                    {"configurable": {"thread_id": "trip-session"}},
                )

        create_category.assert_not_awaited()

    async def test_missing_trip_fails_before_llm_call(self):
        with (
            patch(
                "app.graph.city_profile_node.get_trip_by_session_id",
                new=AsyncMock(return_value=None),
            ),
            patch("app.graph.city_profile_node.call_forced_tool") as llm_call,
        ):
            with self.assertRaisesRegex(ValueError, "No trip found"):
                await city_profile_node(
                    {"destination": "Porto", "trip_length_days": 2},
                    {"configurable": {"thread_id": "missing"}},
                )

        llm_call.assert_not_called()
