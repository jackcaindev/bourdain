from unittest import TestCase

from app.graph.itinerary import MAX_ACTIVITIES_PER_DAY, assemble_itinerary
from app.models.schemas import ScoredRecommendation


def _recommendation(
    recommendation_id: str,
    category: str,
) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=recommendation_id,
        name=recommendation_id.title(),
        category=category,
        description="A selected recommendation.",
        source="vector_store",
        raw_signal="Local evidence.",
        relevance_score=0.9,
        authenticity_signal="Strong local character.",
        confidence="high",
        needs_fallback=False,
        bourdain_score=4,
        scoring_rationale="A strong fit.",
        locally_owned_signal=None,
        passed_guardrail=True,
        guardrail_note=None,
    )


def _assemble(
    recommendations: list[ScoredRecommendation],
    day_count: int,
    selections: list[str] | None = None,
):
    return assemble_itinerary(
        {
            "scored_recommendations": recommendations,
            "user_selections": selections
            if selections is not None
            else [item.id for item in recommendations],
            "trip_length_days": day_count,
        }
    )["itinerary"]


class AssembleItineraryTests(TestCase):
    def test_distributes_meals_and_activities_evenly_across_days(self):
        recommendations = [
            *[_recommendation(f"meal-{index}", "Food") for index in range(6)],
            *[_recommendation(f"activity-{index}", "Culture") for index in range(4)],
        ]

        itinerary = _assemble(recommendations, day_count=2)

        self.assertEqual(
            [
                [day.breakfast.id, day.lunch.id, day.dinner.id]
                for day in itinerary
            ],
            [
                ["meal-0", "meal-2", "meal-4"],
                ["meal-1", "meal-3", "meal-5"],
            ],
        )
        self.assertEqual(
            [[item.id for item in day.activities] for day in itinerary],
            [["activity-0", "activity-2"], ["activity-1", "activity-3"]],
        )

    def test_interleaves_meal_categories_to_avoid_same_day_monoculture(self):
        recommendations = [
            _recommendation("food-0", "Food"),
            _recommendation("restaurant-0", "Restaurant"),
            _recommendation("food-1", "Food"),
            _recommendation("cafe-0", "Cafe"),
            _recommendation("food-2", "Food"),
            _recommendation("restaurant-1", "Restaurant"),
        ]

        itinerary = _assemble(recommendations, day_count=2)

        for day in itinerary:
            meal_categories = {
                day.breakfast.category,
                day.lunch.category,
                day.dinner.category,
            }
            self.assertGreater(len(meal_categories), 1)

    def test_caps_activities_at_two_per_day_and_drops_overflow(self):
        recommendations = [
            _recommendation(f"activity-{index}", "Culture") for index in range(7)
        ]

        itinerary = _assemble(recommendations, day_count=2)

        self.assertTrue(
            all(len(day.activities) == MAX_ACTIVITIES_PER_DAY for day in itinerary)
        )
        assigned_ids = {
            item.id for day in itinerary for item in day.activities
        }
        self.assertEqual(
            assigned_ids,
            {"activity-0", "activity-1", "activity-2", "activity-3"},
        )

    def test_leaves_unfilled_meal_slots_as_none(self):
        itinerary = _assemble(
            [_recommendation("only-meal", "Neighborhood Café")],
            day_count=3,
        )

        self.assertEqual(itinerary[0].breakfast.id, "only-meal")
        self.assertIsNone(itinerary[1].breakfast)
        self.assertIsNone(itinerary[2].breakfast)
        for day in itinerary:
            self.assertIsNone(day.lunch)
            self.assertIsNone(day.dinner)

    def test_uses_unique_majority_category_as_soft_focus_and_none_for_tie(self):
        recommendations = [
            _recommendation("food-one", "Food"),
            _recommendation("food-two", "Food"),
            _recommendation("food-three", "Food"),
            _recommendation("food-four", "Food"),
            _recommendation("museum", "Culture"),
            _recommendation("music", "Music"),
            _recommendation("gallery", "Art"),
            _recommendation("concert", "Music"),
        ]

        itinerary = _assemble(recommendations, day_count=2)

        self.assertEqual(itinerary[0].neighborhood_focus, "Food")
        self.assertIsNone(itinerary[1].neighborhood_focus)

    def test_filters_out_recommendations_not_selected_by_the_user(self):
        recommendations = [
            _recommendation("selected", "Food"),
            _recommendation("not-selected", "Culture"),
        ]

        itinerary = _assemble(
            recommendations,
            day_count=1,
            selections=["selected"],
        )

        self.assertEqual(itinerary[0].breakfast.id, "selected")
        self.assertEqual(itinerary[0].activities, [])
