from uuid import uuid4

from app.db.recommendations import (
    create_recommendation,
    get_recommendation_by_id,
    get_recommendations_by_category,
)
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

    async def test_get_by_category_orders_by_score_and_matches_evidence(self):
        trip_id = await self.insert_trip()
        category_id = await self.insert_category(trip_id)
        other_category_id = await self.insert_category(trip_id)
        run_id = await self.insert_research_run(trip_id, category_id)
        other_run_id = await self.insert_research_run(trip_id, other_category_id)
        lower_place_id = await self.insert_place()
        higher_place_id = await self.insert_place()
        other_place_id = await self.insert_place()

        lower_id = await self.insert_recommendation(
            trip_id, category_id, run_id, lower_place_id
        )
        higher_id = await self.insert_recommendation(
            trip_id, category_id, run_id, higher_place_id
        )
        await self.insert_recommendation(
            trip_id, other_category_id, other_run_id, other_place_id
        )
        await self.connection.execute(
            "UPDATE recommendations SET bourdain_score = 3 WHERE id = $1",
            lower_id,
        )
        await self.connection.execute(
            "UPDATE places SET name = 'Higher-rated place' WHERE id = $1",
            higher_place_id,
        )
        await self.connection.execute(
            """
            INSERT INTO evidence (
                place_id, research_run_id, source_type, raw_content
            ) VALUES
                ($1, $2, 'vector_store', 'Correct editorial evidence'),
                ($1, $2, 'places_api', 'Places payload'),
                ($1, $3, 'web_search', 'Evidence from another run')
            """,
            higher_place_id,
            run_id,
            other_run_id,
        )

        result = await get_recommendations_by_category(trip_id, category_id)

        self.assertEqual([item.id for item in result], [higher_id, lower_id])
        self.assertEqual(result[0].name, "Higher-rated place")
        self.assertEqual(result[0].description, "Correct editorial evidence")
        self.assertNotIn("Places payload", result[0].description)
        self.assertNotIn("another run", result[0].description)
