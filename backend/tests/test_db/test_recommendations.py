from uuid import uuid4

from app.db.recommendations import create_recommendation, get_recommendation_by_id
from tests.test_db.helpers import DatabaseTestCase


class RecommendationDatabaseTests(DatabaseTestCase):
    module_name = "recommendations"

    async def test_create_and_read_recommendation(self):
        trip_id = await self.insert_trip()
        category_id = await self.insert_category(trip_id)
        place_id = await self.insert_place()
        run_id = await self.insert_research_run(trip_id, category_id)

        recommendation = await create_recommendation(
            trip_id=trip_id,
            category_id=category_id,
            research_run_id=run_id,
            place_id=place_id,
            relevance_score=0.95,
            authenticity_signal="Long-running neighborhood business",
            confidence="high",
            needs_fallback=False,
            bourdain_score=5,
            scoring_rationale="Deep local roots and a focused menu.",
            locally_owned_signal="Owner-operated",
            passed_guardrail=True,
        )

        self.assertEqual(
            await get_recommendation_by_id(recommendation.id), recommendation
        )
        self.assertIsNone(await get_recommendation_by_id(uuid4()))
