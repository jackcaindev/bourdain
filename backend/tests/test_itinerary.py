from unittest import TestCase

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
) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=recommendation_id,
        name=recommendation_id.title(),
        category=category,
        description="A selected recommendation.",
        lat=lat,
        lng=lng,
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


def _assemble(
    categories: list[Category],
    recommendations: list[ScoredRecommendation],
    *,
    days: int = 1,
    time_blocks: list[str] | None = None,
    selections: list[str] | None = None,
    destination: tuple[float, float] = (40.0, -74.0),
):
    return assemble_itinerary(
        {
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
    )["itinerary"]


def _slot(day, block):
    return next(slot for slot in day.slots if slot.time_block == block)


class AssembleItineraryTests(TestCase):
    def test_drops_category_without_an_eligible_trip_block(self):
        category = _category("Night music", "activity", ["night"])
        recommendation = _recommendation("music", category.name)

        with self.assertLogs("app.graph.itinerary", level="WARNING"):
            itinerary = _assemble(
                [category], [recommendation], time_blocks=["morning"]
            )

        self.assertEqual(itinerary[0].slots, [])

    def test_highest_bourdain_score_wins_category_occupancy(self):
        category = _category("Museums", "activity", ["morning"])
        lower = _recommendation(
            "lower", category.name, bourdain_score=4, relevance_score=1.0
        )
        winner = _recommendation(
            "winner", category.name, bourdain_score=5, relevance_score=0.1
        )

        itinerary = _assemble([category], [lower, winner])

        self.assertEqual(_slot(itinerary[0], "morning").activity.id, "winner")

    def test_long_activity_with_multiple_eligible_blocks_spans_same_day(self):
        category = _category(
            "Long hike",
            "activity",
            ["morning", "afternoon"],
            duration=300,
        )
        recommendation = _recommendation("hike", category.name)

        itinerary = _assemble([category], [recommendation], days=2)

        self.assertEqual(
            [slot.time_block for slot in itinerary[0].slots],
            ["morning", "afternoon"],
        )
        self.assertTrue(
            all(slot.activity.id == "hike" for slot in itinerary[0].slots)
        )
        self.assertEqual(itinerary[1].slots, [])

    def test_long_single_block_activity_is_not_split_across_days(self):
        category = _category(
            "Long workshop", "activity", ["morning"], duration=500
        )
        recommendation = _recommendation("workshop", category.name)

        itinerary = _assemble([category], [recommendation], days=2)

        occupied_slots = [
            slot
            for day in itinerary
            for slot in day.slots
            if slot.activity is not None
        ]
        self.assertEqual(len(occupied_slots), 1)
        self.assertEqual(occupied_slots[0].activity.id, "workshop")

    def test_same_neighborhood_is_kept_on_one_day_when_an_open_block_exists(self):
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
            duration=60,
            neighborhood="Old Town",
        )
        recommendations = [
            _recommendation("history", morning.name),
            _recommendation("studios", flexible.name),
        ]

        itinerary = _assemble([morning, flexible], recommendations, days=2)

        self.assertEqual(
            {slot.activity.id for slot in itinerary[0].slots},
            {"history", "studios"},
        )
        self.assertEqual(itinerary[1].slots, [])

    def test_food_without_same_block_activity_uses_nearest_activity_day(self):
        activity = _category(
            "River walk", "activity", ["morning"], neighborhood="Riverside"
        )
        food = _category(
            "Lunch counters", "food", ["afternoon"], neighborhood="Riverside"
        )
        recommendations = [
            _recommendation("walk", activity.name, lat=40.01, lng=-74.01),
            _recommendation("lunch", food.name, lat=40.02, lng=-74.02),
        ]

        itinerary = _assemble([activity, food], recommendations, days=2)

        self.assertEqual(_slot(itinerary[0], "afternoon").meals[0].id, "lunch")
        self.assertEqual(itinerary[1].slots, [])

    def test_food_on_trip_without_activities_uses_destination_anchor(self):
        food = _category("Breakfast stalls", "food", ["morning"])
        recommendation = _recommendation(
            "breakfast", food.name, lat=40.001, lng=-74.001
        )

        itinerary = _assemble(
            [food],
            [recommendation],
            days=2,
            destination=(40.0, -74.0),
        )

        self.assertEqual(_slot(itinerary[0], "morning").meals[0].id, "breakfast")
        self.assertEqual(itinerary[1].slots, [])

    def test_neighborhood_focus_is_none_for_equal_counts(self):
        activity = _category(
            "Gallery", "activity", ["morning"], neighborhood="North"
        )
        food = _category(
            "Dinner", "food", ["morning"], neighborhood="South"
        )
        recommendations = [
            _recommendation("gallery", activity.name),
            _recommendation("dinner", food.name),
        ]

        itinerary = _assemble([activity, food], recommendations)

        self.assertIsNone(itinerary[0].neighborhood_focus)
