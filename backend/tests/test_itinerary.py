from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from app.graph.itinerary import assemble_itinerary
from app.models.schemas import Category, ScoredRecommendation


def _recommendation(
    recommendation_id: str,
    category: str,
    *,
    bourdain_score: int = 4,
    relevance_score: float = 0.8,
    lat: float = 40.0,
    lng: float = -74.0,
    db_recommendation_id: UUID | None = None,
) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=recommendation_id,
        name=recommendation_id.title(),
        category=category,
        description="A selected recommendation.",
        lat=lat,
        lng=lng,
        db_recommendation_id=db_recommendation_id,
        source="vector_store",
        raw_signal="Specific local evidence.",
        relevance_score=relevance_score,
        authenticity_signal="Strong local character.",
        confidence="high",
        needs_fallback=False,
        bourdain_score=bourdain_score,
        scoring_rationale="A strong fit.",
        locally_owned_signal=None,
        passed_guardrail=True,
        guardrail_note=None,
    )


def _category(
    name: str,
    category_type: str,
    blocks: list[str],
    *,
    duration: int = 60,
    neighborhood: str = "Centro",
) -> Category:
    return Category(
        name=name,
        type=category_type,
        estimated_duration_minutes=duration,
        eligible_blocks=blocks,
        neighborhood_scope=neighborhood,
    )


async def _assemble(
    categories: list[Category],
    recommendations: list[ScoredRecommendation],
    *,
    days: int = 1,
    time_blocks: list[str] | None = None,
    selections: list[str] | None = None,
    destination: tuple[float, float] = (40.0, -74.0),
    trip_id: UUID | None = None,
):
    result = await assemble_itinerary(
        {
            "trip_id": trip_id or uuid4(),
            "selected_categories": categories,
            "scored_recommendations": recommendations,
            "user_selections": selections
            if selections is not None
            else [recommendation.id for recommendation in recommendations],
            "trip_length_days": days,
            "time_blocks": time_blocks or ["morning", "afternoon", "night"],
            "destination_lat": destination[0],
            "destination_lng": destination[1],
        }
    )
    return result["itinerary"]


def _slot(day, block):
    return next(slot for slot in day.slots if slot.time_block == block)


class AssembleItineraryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.create_itinerary_patcher = patch(
            "app.graph.itinerary.create_itinerary", new_callable=AsyncMock
        )
        self.create_day_patcher = patch(
            "app.graph.itinerary.create_itinerary_day", new_callable=AsyncMock
        )
        self.upsert_activity_patcher = patch(
            "app.graph.itinerary.upsert_activity_slot", new_callable=AsyncMock
        )
        self.create_meal_patcher = patch(
            "app.graph.itinerary.create_meal_slot", new_callable=AsyncMock
        )
        self.create_itinerary = self.create_itinerary_patcher.start()
        self.create_day = self.create_day_patcher.start()
        self.upsert_activity = self.upsert_activity_patcher.start()
        self.create_meal = self.create_meal_patcher.start()
        self.itinerary_id = uuid4()
        self.day_ids: list[UUID] = []
        self.create_itinerary.return_value = SimpleNamespace(id=self.itinerary_id)

        def persisted_day(**_kwargs):
            day_id = uuid4()
            self.day_ids.append(day_id)
            return SimpleNamespace(id=day_id)

        self.create_day.side_effect = persisted_day
        self.addCleanup(self.create_itinerary_patcher.stop)
        self.addCleanup(self.create_day_patcher.stop)
        self.addCleanup(self.upsert_activity_patcher.stop)
        self.addCleanup(self.create_meal_patcher.stop)

    async def test_drops_category_without_an_eligible_trip_block(self):
        category = _category("Night music", "activity", ["night"])
        recommendation = _recommendation("music", category.name)
        with self.assertLogs("app.graph.itinerary", level="WARNING"):
            itinerary = await _assemble(
                [category], [recommendation], time_blocks=["morning"]
            )
        self.assertEqual(itinerary[0].slots, [])

    async def test_highest_bourdain_score_wins_category_occupancy(self):
        category = _category("Museums", "activity", ["morning"])
        lower = _recommendation(
            "lower", category.name, bourdain_score=4, relevance_score=1.0
        )
        winner = _recommendation(
            "winner", category.name, bourdain_score=5, relevance_score=0.1
        )
        itinerary = await _assemble([category], [lower, winner])
        self.assertEqual(_slot(itinerary[0], "morning").activity.id, "winner")

    async def test_long_activity_with_multiple_blocks_spans_same_day(self):
        category = _category(
            "Long hike", "activity", ["morning", "afternoon"], duration=300
        )
        recommendation = _recommendation("hike", category.name)
        itinerary = await _assemble([category], [recommendation], days=2)
        self.assertEqual(
            [slot.time_block for slot in itinerary[0].slots],
            ["morning", "afternoon"],
        )
        self.assertTrue(all(slot.activity.id == "hike" for slot in itinerary[0].slots))
        self.assertEqual(itinerary[1].slots, [])

    async def test_long_single_block_activity_is_not_split_across_days(self):
        category = _category(
            "Long workshop", "activity", ["morning"], duration=500
        )
        itinerary = await _assemble(
            [category], [_recommendation("workshop", category.name)], days=2
        )
        occupied = [
            slot
            for day in itinerary
            for slot in day.slots
            if slot.activity is not None
        ]
        self.assertEqual(len(occupied), 1)

    async def test_same_neighborhood_stays_on_one_day_when_block_is_open(self):
        morning = _category(
            "Morning history",
            "activity",
            ["morning"],
            duration=120,
            neighborhood="Old Town",
        )
        flexible = _category(
            "Local studios",
            "activity",
            ["morning", "afternoon"],
            neighborhood="Old Town",
        )
        itinerary = await _assemble(
            [morning, flexible],
            [
                _recommendation("history", morning.name),
                _recommendation("studios", flexible.name),
            ],
            days=2,
        )
        self.assertEqual(
            {slot.activity.id for slot in itinerary[0].slots},
            {"history", "studios"},
        )
        self.assertEqual(itinerary[1].slots, [])

    async def test_food_without_same_block_activity_uses_activity_day(self):
        activity = _category("River walk", "activity", ["morning"])
        food = _category("Lunch counters", "food", ["afternoon"])
        itinerary = await _assemble(
            [activity, food],
            [
                _recommendation("walk", activity.name, lat=40.01, lng=-74.01),
                _recommendation("lunch", food.name, lat=40.02, lng=-74.02),
            ],
            days=2,
        )
        self.assertEqual(_slot(itinerary[0], "afternoon").meals[0].id, "lunch")
        self.assertEqual(itinerary[1].slots, [])

    async def test_food_without_activities_uses_destination_anchor(self):
        food = _category("Breakfast stalls", "food", ["morning"])
        itinerary = await _assemble(
            [food],
            [_recommendation("breakfast", food.name, lat=40.001, lng=-74.001)],
            days=2,
        )
        self.assertEqual(_slot(itinerary[0], "morning").meals[0].id, "breakfast")

    async def test_neighborhood_focus_is_none_for_equal_counts(self):
        activity = _category(
            "Gallery", "activity", ["morning"], neighborhood="North"
        )
        food = _category("Dinner", "food", ["morning"], neighborhood="South")
        itinerary = await _assemble(
            [activity, food],
            [
                _recommendation("gallery", activity.name),
                _recommendation("dinner", food.name),
            ],
        )
        self.assertIsNone(itinerary[0].neighborhood_focus)

    async def test_persists_itinerary_days_activity_and_meal_slots(self):
        trip_id = uuid4()
        activity_id = uuid4()
        meal_id = uuid4()
        activity = _category("Gallery", "activity", ["morning"])
        food = _category("Breakfast", "food", ["morning"])

        itinerary = await _assemble(
            [activity, food],
            [
                _recommendation(
                    "gallery", activity.name, db_recommendation_id=activity_id
                ),
                _recommendation(
                    "breakfast", food.name, db_recommendation_id=meal_id
                ),
            ],
            days=2,
            trip_id=trip_id,
        )

        self.assertEqual(len(itinerary), 2)
        self.create_itinerary.assert_awaited_once_with(trip_id=trip_id)
        self.assertEqual(self.create_day.await_count, 2)
        self.assertEqual(
            [call.kwargs["day_number"] for call in self.create_day.await_args_list],
            [1, 2],
        )
        self.upsert_activity.assert_awaited_once_with(
            itinerary_day_id=self.day_ids[0],
            time_block="morning",
            recommendation_id=activity_id,
        )
        self.create_meal.assert_awaited_once_with(
            itinerary_day_id=self.day_ids[0],
            time_block="morning",
            recommendation_id=meal_id,
        )

    async def test_two_meals_in_one_block_create_two_rows(self):
        first_id = uuid4()
        second_id = uuid4()
        first = _category("Coffee", "food", ["morning"])
        second = _category("Breakfast", "food", ["morning"])

        await _assemble(
            [first, second],
            [
                _recommendation("coffee", first.name, db_recommendation_id=first_id),
                _recommendation(
                    "breakfast", second.name, db_recommendation_id=second_id
                ),
            ],
        )

        self.assertEqual(self.create_meal.await_count, 2)
        self.assertEqual(
            [call.kwargs["recommendation_id"] for call in self.create_meal.await_args_list],
            [first_id, second_id],
        )

    async def test_missing_db_recommendation_id_warns_and_skips_slot(self):
        activity = _category("Gallery", "activity", ["morning"])
        with self.assertLogs("app.graph.itinerary", level="WARNING") as logs:
            await _assemble(
                [activity], [_recommendation("gallery", activity.name)]
            )
        self.assertTrue(
            any("itinerary_slot_persistence_skipped" in line for line in logs.output)
        )
        self.upsert_activity.assert_not_awaited()
        self.create_meal.assert_not_awaited()
