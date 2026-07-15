from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.graph.city_profile_node import (
    _category_tool_schema,
    _target_category_count,
    city_profile_node,
)
from app.models.schemas import Category


def _categories(count: int = 5) -> list[Category]:
    return [
        Category(name=f"Category {index}", rationale=f"Rationale {index}")
        for index in range(count)
    ]


class CityProfilePromptTests(TestCase):
    def test_category_count_scales_between_five_and_eight(self):
        self.assertEqual(_target_category_count(1), 5)
        self.assertEqual(_target_category_count(3), 6)
        self.assertEqual(_target_category_count(5), 7)
        self.assertEqual(_target_category_count(7), 8)
        self.assertEqual(_target_category_count(30), 8)

    def test_tool_schema_requires_five_to_eight_categories(self):
        categories_schema = _category_tool_schema()["input_schema"]["properties"][
            "categories"
        ]
        self.assertEqual(categories_schema["minItems"], 5)
        self.assertEqual(categories_schema["maxItems"], 8)
        self.assertEqual(
            categories_schema["items"]["required"], ["name", "rationale"]
        )


class CityProfileNodeTests(IsolatedAsyncioTestCase):
    async def test_cache_hit_returns_without_llm_or_save(self):
        stored_categories = _categories()

        with (
            patch(
                "app.graph.city_profile_node.get_city_profile",
                new=AsyncMock(return_value=stored_categories),
            ) as get_profile,
            patch("app.graph.city_profile_node.call_forced_tool") as llm_call,
            patch(
                "app.graph.city_profile_node.save_city_profile",
                new_callable=AsyncMock,
            ) as save_profile,
        ):
            result = await city_profile_node(
                {"destination": "Austin, TX", "trip_length_days": 3}
            )

        self.assertEqual(
            result,
            {
                "city_slug": "austin-tx",
                "categories": stored_categories,
                "research_iteration": 0,
                "replan_categories": [],
                "selected_categories": None,
            },
        )
        get_profile.assert_awaited_once_with("austin-tx")
        llm_call.assert_not_called()
        save_profile.assert_not_awaited()

    async def test_cache_miss_derives_saves_and_returns_categories(self):
        derived_categories = _categories(6)
        tool_input = {
            "categories": [
                category.model_dump(mode="json") for category in derived_categories
            ]
        }

        with (
            patch(
                "app.graph.city_profile_node.get_city_profile",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.graph.city_profile_node.call_forced_tool",
                return_value=tool_input,
            ) as llm_call,
            patch(
                "app.graph.city_profile_node.save_city_profile",
                new_callable=AsyncMock,
            ) as save_profile,
        ):
            result = await city_profile_node(
                {"destination": "Oaxaca, Mexico", "trip_length_days": 4}
            )

        self.assertEqual(result["city_slug"], "oaxaca-mexico")
        self.assertEqual(result["categories"], derived_categories)
        self.assertEqual(result["research_iteration"], 0)
        self.assertEqual(result["replan_categories"], [])
        self.assertIsNone(result["selected_categories"])
        llm_call.assert_called_once()
        call_kwargs = llm_call.call_args.kwargs
        self.assertIn("Derive 6 distinct research categories", call_kwargs["user_prompt"])
        self.assertIn("non-touristy", call_kwargs["system_prompt"])
        save_profile.assert_awaited_once_with(
            "oaxaca-mexico", "Oaxaca, Mexico", derived_categories
        )

    async def test_malformed_llm_categories_raise_without_saving(self):
        with (
            patch(
                "app.graph.city_profile_node.get_city_profile",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.graph.city_profile_node.call_forced_tool",
                return_value={
                    "categories": [
                        {"name": "Too broad", "rationale": "Only one"}
                    ]
                },
            ),
            patch(
                "app.graph.city_profile_node.save_city_profile",
                new_callable=AsyncMock,
            ) as save_profile,
        ):
            with self.assertRaises(ValidationError):
                await city_profile_node(
                    {"destination": "Porto", "trip_length_days": 2}
                )

        save_profile.assert_not_awaited()

    async def test_llm_and_database_failures_propagate(self):
        with (
            patch(
                "app.graph.city_profile_node.get_city_profile",
                new=AsyncMock(side_effect=RuntimeError("database down")),
            ),
            patch("app.graph.city_profile_node.call_forced_tool") as llm_call,
        ):
            with self.assertRaisesRegex(RuntimeError, "database down"):
                await city_profile_node(
                    {"destination": "Porto", "trip_length_days": 2}
                )
        llm_call.assert_not_called()

        with (
            patch(
                "app.graph.city_profile_node.get_city_profile",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.graph.city_profile_node.call_forced_tool",
                side_effect=RuntimeError("LLM down"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "LLM down"):
                await city_profile_node(
                    {"destination": "Porto", "trip_length_days": 2}
                )

    async def test_save_failure_propagates(self):
        derived_categories = _categories()
        with (
            patch(
                "app.graph.city_profile_node.get_city_profile",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.graph.city_profile_node.call_forced_tool",
                return_value={
                    "categories": [
                        category.model_dump(mode="json")
                        for category in derived_categories
                    ]
                },
            ),
            patch(
                "app.graph.city_profile_node.save_city_profile",
                new=AsyncMock(side_effect=RuntimeError("write failed")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                await city_profile_node(
                    {"destination": "Porto", "trip_length_days": 2}
                )
