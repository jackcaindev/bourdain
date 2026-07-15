import json
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from app.graph.research import ResearchCategoryError, _grade_candidates, research_category
from app.models.schemas import Candidate, Category, ScoredRecommendation
from app.services.vector_store import VectorSearchResult
from app.services.web_search import WebSearchResult


def _result(name="Cafe"):
    return VectorSearchResult(
        id=uuid4(),
        name=name,
        content="A specific neighborhood institution.",
        category="food",
        metadata={"url": "https://local.example/cafe"},
        distance=0.1,
    )


def _tool_output(candidate_ids, *, fallback=False):
    return {
        "grades": [
            {
                "candidate_id": candidate_id,
                "relevance_score": 0.8,
                "authenticity_signal": "Specific local evidence",
                "confidence": "high",
                "needs_fallback": fallback,
            }
            for candidate_id in candidate_ids
        ]
    }


def _scored_recommendation(
    recommendation_id: str = "cached-id",
    *,
    name: str = "Cached Cafe",
    source: str = "cache",
) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=recommendation_id,
        name=name,
        category="Late-night food",
        description="A cached neighborhood spot.",
        source=source,
        raw_signal="Cached local evidence.",
        relevance_score=0.9,
        authenticity_signal="Cached authenticity signal.",
        confidence="high",
        needs_fallback=False,
        bourdain_score=4,
        scoring_rationale="Cached scoring rationale.",
        passed_guardrail=True,
    )


async def _mock_scoring_node(state):
    return {
        "scored_recommendations": [
            ScoredRecommendation(
                **candidate.model_dump(),
                bourdain_score=4,
                scoring_rationale="Test rationale",
                passed_guardrail=True,
            )
            for candidate in state["graded_candidates"]
        ]
    }


def _mock_guardrail_node(state):
    return {"scored_recommendations": state["scored_recommendations"]}


class ResearchCategoryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.category = Category(name="Late-night food", rationale="Find worker-focused spots")
        self.pool = MagicMock()
        self.pool.close = AsyncMock()

        cache_get = patch(
            "app.graph.research.get_cached_recommendations",
            new_callable=AsyncMock,
            return_value=None,
        )
        cache_write = patch(
            "app.graph.research.write_category_cache",
            new_callable=AsyncMock,
        )
        geocode = patch(
            "app.graph.research.geocode_venue",
            new_callable=AsyncMock,
            return_value=None,
        )
        scoring = patch(
            "app.graph.research.scoring_node",
            new_callable=AsyncMock,
            side_effect=_mock_scoring_node,
        )
        guardrail = patch(
            "app.graph.research.guardrail_node",
            side_effect=_mock_guardrail_node,
        )
        vector_insert = patch(
            "app.graph.research.insert_candidate",
            new_callable=AsyncMock,
        )

        self.get_cached_recommendations = cache_get.start()
        self.write_category_cache = cache_write.start()
        self.geocode_venue = geocode.start()
        self.scoring_node = scoring.start()
        self.guardrail_node = guardrail.start()
        self.insert_candidate = vector_insert.start()
        self.addCleanup(cache_get.stop)
        self.addCleanup(cache_write.stop)
        self.addCleanup(geocode.stop)
        self.addCleanup(scoring.stop)
        self.addCleanup(guardrail.stop)
        self.addCleanup(vector_insert.stop)

    @patch("app.graph.research.run_web_fallback_agent", new_callable=AsyncMock)
    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_grades_vector_results_once_without_fallback(
        self, embed, get_shared_pool, query, grade, search
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        vector_result = _result()
        query.return_value = [vector_result]
        grade.return_value = _tool_output([str(vector_result.id)])

        result = await research_category(self.category, city_slug="test-city", city_name="Test City")

        embed.assert_called_once_with(["Late-night food: Find worker-focused spots"])
        query.assert_awaited_once_with(
            self.pool,
            query_embedding=embed.return_value[0],
            city_slug="test-city",
            top_k=5,
        )
        self.assertEqual(grade.call_count, 1)
        search.assert_not_called()
        self.assertEqual(result["scored_recommendations"][0].source, "vector_store")
        self.pool.close.assert_not_awaited()

    @patch("app.graph.research.run_web_fallback_agent", new_callable=AsyncMock)
    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_one_category_fallback_and_combined_regrade(
        self, embed, get_shared_pool, query, grade, search
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        vector_results = [_result("Cafe"), _result("Diner")]
        query.return_value = vector_results

        def grade_batch(**kwargs):
            if kwargs["tool_schema"]["name"] == "extract_venues":
                return {
                    "venues": [
                        {
                            "name": "Stall",
                            "description": "Night stall",
                            "source_url": "https://web.example",
                        }
                    ]
                }
            candidates_json = kwargs["user_prompt"].split("Candidates:\n", 1)[1]
            candidate_ids = [item["id"] for item in json.loads(candidates_json)]
            return _tool_output(
                candidate_ids,
                fallback=grade.call_count == 1,
            )

        grade.side_effect = grade_batch
        search.return_value = [
            WebSearchResult(title="Stall", url="https://web.example", content="Night stall")
        ]

        result = await research_category(self.category, city_slug="test-city", city_name="Test City")

        search.assert_awaited_once_with(self.category, "Test City")
        self.assertEqual(grade.call_count, 3)
        self.assertEqual(len(result["scored_recommendations"]), 3)
        self.assertEqual(result["scored_recommendations"][-1].source, "web_search")

    @patch("app.graph.research.run_web_fallback_agent", new_callable=AsyncMock)
    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_empty_vector_results_trigger_fallback(
        self, embed, get_shared_pool, query, grade, search
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        query.return_value = []

        def grade_batch(**kwargs):
            if kwargs["tool_schema"]["name"] == "extract_venues":
                return {
                    "venues": [
                        {
                            "name": "Night Market",
                            "description": "A late-night market serving local workers.",
                            "source_url": "https://web.example/night-market",
                        }
                    ]
                }
            candidates_json = kwargs["user_prompt"].split("Candidates:\n", 1)[1]
            candidate_ids = [item["id"] for item in json.loads(candidates_json)]
            return _tool_output(candidate_ids)

        grade.side_effect = grade_batch
        search.return_value = [
            WebSearchResult(
                title="Night Market",
                url="https://web.example/night-market",
                content="A late-night market serving local workers.",
            )
        ]

        result = await research_category(
            self.category, city_slug="test-city", city_name="Test City"
        )

        search.assert_awaited_once_with(self.category, "Test City")
        self.assertEqual(grade.call_count, 3)
        self.assertEqual(len(result["scored_recommendations"]), 1)
        self.assertEqual(result["scored_recommendations"][0].source, "web_search")

    @patch("app.graph.research.run_web_fallback_agent", new_callable=AsyncMock)
    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_roundup_result_extracts_multiple_named_candidates(
        self, embed, get_shared_pool, query, tool_call, search
    ):
        embed.side_effect = [
            [[0.0] * 1536],
            [[0.1] * 1536, [0.2] * 1536],
        ]
        get_shared_pool.return_value = self.pool
        query.return_value = []
        search.return_value = [
            WebSearchResult(
                title="Three essential late-night stops",
                url="https://web.example/roundup",
                content=(
                    "Visit Moonlight Diner for counter-service breakfast, then try "
                    "Harbor Noodles for hand-pulled noodles after midnight."
                ),
            )
        ]

        def tool_output(**kwargs):
            if kwargs["tool_schema"]["name"] == "extract_venues":
                return {
                    "venues": [
                        {
                            "name": "Moonlight Diner",
                            "description": "Counter-service breakfast late at night.",
                            "source_url": "https://web.example/roundup",
                        },
                        {
                            "name": "Harbor Noodles",
                            "description": "Hand-pulled noodles served after midnight.",
                            "source_url": "https://web.example/roundup",
                        },
                    ]
                }
            candidates_json = kwargs["user_prompt"].split("Candidates:\n", 1)[1]
            candidate_ids = [item["id"] for item in json.loads(candidates_json)]
            return _tool_output(candidate_ids)

        tool_call.side_effect = tool_output

        result = await research_category(
            self.category, city_slug="test-city", city_name="Test City"
        )

        recommendations = result["scored_recommendations"]
        self.assertEqual(
            [recommendation.name for recommendation in recommendations],
            ["Moonlight Diner", "Harbor Noodles"],
        )
        self.assertTrue(
            all(recommendation.source == "web_search" for recommendation in recommendations)
        )
        extraction_call = next(
            call
            for call in tool_call.call_args_list
            if call.kwargs["tool_schema"]["name"] == "extract_venues"
        )
        self.assertIn("Three essential late-night stops", extraction_call.kwargs["user_prompt"])

    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_writes_only_guardrail_passed_web_results_to_vector_store(
        self, embed, get_shared_pool, query, grade
    ):
        query_embedding = [0.0] * 1536
        web_embedding = [0.5] * 1536
        embed.side_effect = [[query_embedding], [web_embedding]]
        get_shared_pool.return_value = self.pool
        vector_result = _result()
        query.return_value = [vector_result]
        grade.return_value = _tool_output([str(vector_result.id)])

        passed_web = _scored_recommendation(
            str(uuid4()), name="Passed Web", source="web_search"
        ).model_copy(update={"source_url": "https://web.example/passed"})
        vector_store = _scored_recommendation(
            str(uuid4()), name="Vector Result", source="vector_store"
        )
        cache = _scored_recommendation(
            str(uuid4()), name="Cache Result", source="cache"
        )
        failed_web = _scored_recommendation(
            str(uuid4()), name="Failed Web", source="web_search"
        ).model_copy(update={"passed_guardrail": False})
        self.guardrail_node.side_effect = lambda _state: {
            "scored_recommendations": [
                passed_web,
                vector_store,
                cache,
                failed_web,
            ]
        }

        await research_category(
            self.category, city_slug="test-city", city_name="Test City"
        )

        self.assertEqual(embed.call_count, 2)
        embed.assert_any_call([passed_web.description])
        self.insert_candidate.assert_awaited_once_with(
            self.pool,
            name=passed_web.name,
            content=passed_web.description,
            category=self.category.name,
            city_slug="test-city",
            embedding=web_embedding,
            metadata={"source_url": passed_web.source_url},
            candidate_id=UUID(passed_web.id),
        )

    @patch("app.graph.research.call_forced_tool", side_effect=RuntimeError("bad tool"))
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_grader_failure_retries_once_and_raises(
        self, embed, get_shared_pool, query, grade
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        query.return_value = [_result()]

        with self.assertRaisesRegex(ResearchCategoryError, "after one retry"):
            await research_category(self.category, city_slug="test-city", city_name="Test City")

        self.assertEqual(grade.call_count, 2)
        self.pool.close.assert_not_awaited()

    @patch("app.graph.research.create_embeddings", side_effect=RuntimeError("api down"))
    async def test_embedding_failure_is_visible(self, embed):
        with self.assertRaisesRegex(ResearchCategoryError, "Embedding failed"):
            await research_category(self.category, city_slug="test-city", city_name="Test City")

    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_cache_hit_returns_early(self, embed, query, grade):
        cached = [_scored_recommendation(source="web_search")]
        self.get_cached_recommendations.return_value = cached

        result = await research_category(
            self.category, city_slug="test-city", city_name="Test City"
        )

        self.assertEqual(result["scored_recommendations"][0].source, "cache")
        self.assertEqual(cached[0].source, "web_search")
        embed.assert_not_called()
        query.assert_not_called()
        grade.assert_not_called()
        self.scoring_node.assert_not_called()
        self.write_category_cache.assert_not_awaited()
        self.geocode_venue.assert_not_awaited()


class GradeMatchingTests(TestCase):
    def setUp(self):
        self.category = Category(name="Food", rationale="Find local institutions")
        self.candidates = [
            Candidate(
                id=candidate_id,
                name=name,
                category="Food",
                description=name,
                source="vector_store",
                raw_signal=name,
            )
            for candidate_id, name in (("first-id", "First"), ("second-id", "Second"))
        ]

    @patch("app.graph.research.call_forced_tool")
    def test_matches_reordered_grades_by_candidate_id(self, grade):
        grade.return_value = {
            "grades": [
                {
                    **_tool_output(["second-id"])["grades"][0],
                    "relevance_score": 0.2,
                },
                {
                    **_tool_output(["first-id"])["grades"][0],
                    "relevance_score": 0.9,
                },
            ]
        }

        result = _grade_candidates(self.category, self.candidates)

        self.assertEqual([item.id for item in result], ["first-id", "second-id"])
        self.assertEqual([item.relevance_score for item in result], [0.9, 0.2])

    @patch("app.graph.research.call_forced_tool")
    def test_unknown_candidate_id_retries_then_raises(self, grade):
        grade.return_value = _tool_output(["first-id", "unknown-id"])

        with self.assertRaisesRegex(ResearchCategoryError, "after one retry") as raised:
            _grade_candidates(self.category, self.candidates)

        self.assertEqual(grade.call_count, 2)
        self.assertIn("unknown candidate_id", str(raised.exception.__cause__))

    @patch("app.graph.research.call_forced_tool")
    def test_missing_candidate_grade_retries_then_raises(self, grade):
        grade.return_value = _tool_output(["first-id"])

        with self.assertRaisesRegex(ResearchCategoryError, "after one retry") as raised:
            _grade_candidates(self.category, self.candidates)

        self.assertEqual(grade.call_count, 2)
        self.assertIn("second-id", str(raised.exception.__cause__))
