import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from app.graph.research import (
    ResearchCategoryError,
    _extract_venues,
    _grade_candidates,
    research_category,
)
from app.models.schemas import Candidate, Category, ScoredRecommendation
from app.services.places import CityResolution, PlaceMatch
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
    recommendation_id: str = "recommendation-id",
    *,
    name: str = "Local Cafe",
    source: str = "vector_store",
    relevance_score: float = 0.9,
    bourdain_score: int = 4,
    passed_guardrail: bool = True,
) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=recommendation_id,
        name=name,
        category="Late-night food",
        description="A neighborhood spot.",
        internal_place_id=uuid4(),
        place_id=f"google-{recommendation_id}",
        formatted_address="1 Test Street, Test City",
        google_types=["restaurant"],
        source=source,
        raw_signal="Specific local evidence.",
        relevance_score=relevance_score,
        authenticity_signal="Local authenticity signal.",
        confidence="high",
        needs_fallback=False,
        bourdain_score=bourdain_score,
        scoring_rationale="Cached scoring rationale.",
        passed_guardrail=passed_guardrail,
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
        self.trip_id = uuid4()
        self.category = Category(
            id=uuid4(),
            name="Late-night food",
            rationale="Find worker-focused spots",
            neighborhood_scope="Old Town",
        )
        self.pool = MagicMock()
        self.pool.close = AsyncMock()

        verify = patch(
            "app.graph.research.verify_venue",
            new_callable=AsyncMock,
        )
        resolve = patch("app.graph.research.resolve_city", new_callable=AsyncMock)
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
        get_place = patch(
            "app.graph.research.get_or_create_place",
            new_callable=AsyncMock,
        )
        create_run = patch(
            "app.graph.research.create_research_run",
            new_callable=AsyncMock,
        )
        complete_run = patch(
            "app.graph.research.complete_research_run",
            new_callable=AsyncMock,
        )
        create_evidence = patch(
            "app.graph.research.create_evidence",
            new_callable=AsyncMock,
        )
        create_recommendation = patch(
            "app.graph.research.create_recommendation",
            new_callable=AsyncMock,
        )

        self.verify_venue = verify.start()
        self.resolve_city = resolve.start()
        self.verify_venue.side_effect = lambda name, **_kwargs: PlaceMatch(
            google_place_id=f"place-{name}",
            name=name,
            formatted_address=f"{name}, Test City",
            lat=10.0,
            lng=20.0,
            google_types=["restaurant"],
        )
        self.resolve_city.return_value = CityResolution(
            status="resolved",
            match=PlaceMatch(
                google_place_id="test-city",
                name="Test City",
                formatted_address="Test City",
                lat=10.0,
                lng=20.0,
                google_types=["locality"],
            ),
        )
        self.scoring_node = scoring.start()
        self.guardrail_node = guardrail.start()
        self.insert_candidate = vector_insert.start()
        self.get_or_create_place = get_place.start()
        self.create_research_run = create_run.start()
        self.complete_research_run = complete_run.start()
        self.create_evidence = create_evidence.start()
        self.create_recommendation = create_recommendation.start()
        self.place_ids_by_google: dict[str, UUID] = {}

        def persisted_place(**kwargs):
            place_id = self.place_ids_by_google.setdefault(
                kwargs["google_place_id"], uuid4()
            )
            return SimpleNamespace(id=place_id)

        self.get_or_create_place.side_effect = persisted_place
        self.created_run_ids: list[UUID] = []

        def persisted_run(**_kwargs):
            run_id = uuid4()
            self.created_run_ids.append(run_id)
            return SimpleNamespace(id=run_id)

        self.create_research_run.side_effect = persisted_run
        self.addCleanup(verify.stop)
        self.addCleanup(resolve.stop)
        self.addCleanup(scoring.stop)
        self.addCleanup(guardrail.stop)
        self.addCleanup(vector_insert.stop)
        self.addCleanup(get_place.stop)
        self.addCleanup(create_run.stop)
        self.addCleanup(complete_run.stop)
        self.addCleanup(create_evidence.stop)
        self.addCleanup(create_recommendation.stop)

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
        grade.return_value = _tool_output([1])

        result = await research_category(
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
        )

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
        self.assertEqual(result["scored_recommendations"][0].place_id, "place-Cafe")
        self.assertEqual(
            result["scored_recommendations"][0].internal_place_id,
            self.place_ids_by_google["place-Cafe"],
        )
        self.get_or_create_place.assert_awaited_once_with(
            google_place_id="place-Cafe",
            name="Cafe",
            formatted_address="Cafe, Test City",
            lat=10.0,
            lng=20.0,
            google_types=["restaurant"],
        )
        self.create_research_run.assert_awaited_once_with(
            trip_id=self.trip_id,
            category_id=self.category.id,
            trigger_reason="initial",
        )
        self.complete_research_run.assert_awaited_once_with(
            self.created_run_ids[0]
        )
        self.assertEqual(self.create_recommendation.await_count, 1)
        self.assertEqual(
            self.create_recommendation.await_args.kwargs["research_run_id"],
            self.created_run_ids[0],
        )
        self.assertTrue(self.verify_venue.await_args_list)
        for verification_call in self.verify_venue.await_args_list:
            self.assertEqual(
                verification_call.kwargs["location_bias"], (10.0, 20.0)
            )
            self.assertEqual(
                verification_call.kwargs["neighborhood_scope"], "Old Town"
            )
        self.pool.close.assert_not_awaited()

    @patch("app.graph.research.run_web_fallback_agent", new_callable=AsyncMock)
    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_multi_category_research_reuses_supplied_destination_coordinates(
        self, embed, get_shared_pool, query, grade, search
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        query.side_effect = [[_result("Cafe")], [_result("Market")]]
        grade.return_value = _tool_output([1])
        categories = [
            self.category,
            Category(
                id=uuid4(),
                name="Neighborhood markets",
                rationale="Find everyday local markets",
                neighborhood_scope="Old Town",
            ),
        ]

        await asyncio.gather(
            *(
                research_category(
                    category,
                    city_slug="test-city",
                    city_name="Test City",
                    trip_id=self.trip_id,
                    trigger_reason="initial",
                    location_bias=(10.0, 20.0),
                )
                for category in categories
            )
        )

        self.assertLessEqual(self.resolve_city.await_count, 1)
        self.assertEqual(self.verify_venue.await_count, 2)
        search.assert_not_awaited()

    @patch("app.graph.research.run_web_fallback_agent", new_callable=AsyncMock)
    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_unverified_candidate_is_dropped_before_grading(
        self, embed, get_shared_pool, query, grade, search
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        query.return_value = [_result("Missing Cafe"), _result("Real Diner")]
        self.verify_venue.side_effect = [
            None,
            PlaceMatch(
                google_place_id="real-diner",
                name="Real Diner",
                formatted_address="Real Diner, Old Town, Test City",
                lat=10.1,
                lng=20.1,
                google_types=["restaurant"],
            ),
        ]
        grade.return_value = _tool_output([1])

        result = await research_category(
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
        )

        grading_payload = json.loads(
            grade.call_args.kwargs["user_prompt"].split("Candidates:\n", 1)[1]
        )
        self.assertEqual([item["name"] for item in grading_payload], ["Real Diner"])
        self.assertEqual(
            [item.name for item in result["scored_recommendations"]], ["Real Diner"]
        )
        search.assert_not_awaited()

    @patch("app.graph.research.run_web_fallback_agent", new_callable=AsyncMock)
    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_all_unverified_vector_candidates_trigger_existing_web_fallback(
        self, embed, get_shared_pool, query, tool_call, search
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        query.return_value = [_result("Imaginary Cafe")]
        verified_web_match = PlaceMatch(
            google_place_id="night-market",
            name="Night Market",
            formatted_address="Night Market, Old Town, Test City",
            lat=10.2,
            lng=20.2,
            google_types=["market"],
        )
        self.verify_venue.side_effect = [None, verified_web_match]
        search.return_value = [
            WebSearchResult(
                title="Night Market",
                url="https://web.example/night-market",
                content="A real market serving late-shift workers.",
            )
        ]

        def tool_output(**kwargs):
            if kwargs["tool_schema"]["name"] == "extract_venues":
                return {
                    "venues": [
                        {
                            "name": "Night Market",
                            "description": "A real market serving late-shift workers.",
                            "source_url": "https://web.example/night-market",
                        }
                    ]
                }
            return _tool_output([1])

        tool_call.side_effect = tool_output

        result = await research_category(
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
        )

        search.assert_awaited_once_with(self.category, "Test City")
        self.assertEqual(tool_call.call_count, 2)
        self.assertEqual(
            [item.name for item in result["scored_recommendations"]], ["Night Market"]
        )
        self.assertEqual(
            [call.kwargs["location_bias"] for call in self.verify_venue.await_args_list],
            [(10.0, 20.0), (10.0, 20.0)],
        )

    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_filters_and_sorts_before_return(
        self, embed, get_shared_pool, query, grade
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        vector_result = _result()
        query.return_value = [vector_result]
        grade.return_value = _tool_output([1])

        failed = _scored_recommendation(
            "failed",
            name="Failed",
            source="vector_store",
            relevance_score=1.0,
            bourdain_score=5,
            passed_guardrail=False,
        )
        score_four_low_relevance = _scored_recommendation(
            "four-low",
            name="Four Low",
            source="vector_store",
            relevance_score=0.2,
            bourdain_score=4,
        )
        score_three = _scored_recommendation(
            "three",
            name="Three",
            source="vector_store",
            relevance_score=0.9,
            bourdain_score=3,
        )
        score_four_high_relevance = _scored_recommendation(
            "four-high",
            name="Four High",
            source="vector_store",
            relevance_score=0.8,
            bourdain_score=4,
        )
        self.guardrail_node.side_effect = lambda _state: {
            "scored_recommendations": [
                failed,
                score_four_low_relevance,
                score_three,
                score_four_high_relevance,
            ]
        }

        result = await research_category(
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
        )

        expected = [
            score_four_high_relevance,
            score_four_low_relevance,
            score_three,
        ]
        self.assertEqual(result["scored_recommendations"], expected)
        self.assertNotIn(failed, result["scored_recommendations"])
        failed_persistence = next(
            call
            for call in self.create_recommendation.await_args_list
            if call.kwargs["place_id"] == failed.internal_place_id
        )
        self.assertFalse(failed_persistence.kwargs["passed_guardrail"])
        self.assertEqual(self.create_recommendation.await_count, 4)
        self.assertEqual(self.create_evidence.await_count, 8)
        self.insert_candidate.assert_not_awaited()

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
            candidate_ids = [
                item["candidate_id"] for item in json.loads(candidates_json)
            ]
            return _tool_output(
                candidate_ids,
                fallback=grade.call_count == 1,
            )

        grade.side_effect = grade_batch
        search.return_value = [
            WebSearchResult(title="Stall", url="https://web.example", content="Night stall")
        ]

        result = await research_category(
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
        )

        search.assert_awaited_once_with(self.category, "Test City")
        self.assertEqual(grade.call_count, 3)
        self.assertEqual(len(result["scored_recommendations"]), 3)
        self.assertEqual(result["scored_recommendations"][-1].source, "web_search")
        self.assertEqual(self.create_research_run.await_count, 2)
        self.assertEqual(
            [call.kwargs["trigger_reason"] for call in self.create_research_run.await_args_list],
            ["initial", "crag_fallback"],
        )
        self.assertEqual(
            [call.args[0] for call in self.complete_research_run.await_args_list],
            self.created_run_ids,
        )
        self.assertTrue(self.create_recommendation.await_args_list)
        self.assertTrue(
            all(
                call.kwargs["research_run_id"] == self.created_run_ids[1]
                for call in self.create_recommendation.await_args_list
            )
        )

    async def test_unpersisted_category_fails_before_research_run_creation(self):
        category = self.category.model_copy(update={"id": None})

        with self.assertRaisesRegex(
            ResearchCategoryError, "must be persisted before research"
        ):
            await research_category(
                category,
                city_slug="test-city",
                city_name="Test City",
                trip_id=self.trip_id,
                trigger_reason="initial",
            )

        self.create_research_run.assert_not_awaited()

    @patch("app.graph.research.call_forced_tool")
    @patch("app.graph.research.query_nearest_neighbors", new_callable=AsyncMock)
    @patch("app.graph.research.get_shared_pool", new_callable=AsyncMock)
    @patch("app.graph.research.create_embeddings")
    async def test_missing_internal_place_id_warns_and_skips_persistence(
        self, embed, get_shared_pool, query, grade
    ):
        embed.return_value = [[0.0] * 1536]
        get_shared_pool.return_value = self.pool
        query.return_value = [_result()]
        grade.return_value = _tool_output([1])
        missing_place = _scored_recommendation().model_copy(
            update={"internal_place_id": None}
        )
        self.guardrail_node.side_effect = lambda _state: {
            "scored_recommendations": [missing_place]
        }

        with self.assertLogs("app.graph.research", level="WARNING") as logs:
            result = await research_category(
                self.category,
                city_slug="test-city",
                city_name="Test City",
                trip_id=self.trip_id,
                trigger_reason="initial",
            )

        self.assertEqual(result["scored_recommendations"], [missing_place])
        self.assertTrue(
            any("research_recommendation_persistence_skipped" in line for line in logs.output)
        )
        self.create_evidence.assert_not_awaited()
        self.create_recommendation.assert_not_awaited()

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
            candidate_ids = [
                item["candidate_id"] for item in json.loads(candidates_json)
            ]
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
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
        )

        search.assert_awaited_once_with(self.category, "Test City")
        self.assertEqual(grade.call_count, 2)
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
            candidate_ids = [
                item["candidate_id"] for item in json.loads(candidates_json)
            ]
            return _tool_output(candidate_ids)

        tool_call.side_effect = tool_output

        result = await research_category(
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
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
        grade.return_value = _tool_output([1])

        passed_web = _scored_recommendation(
            str(uuid4()), name="Passed Web", source="web_search"
        ).model_copy(update={"source_url": "https://web.example/passed"})
        vector_store = _scored_recommendation(
            str(uuid4()), name="Vector Result", source="vector_store"
        )
        failed_web = _scored_recommendation(
            str(uuid4()), name="Failed Web", source="web_search"
        ).model_copy(update={"passed_guardrail": False})
        self.guardrail_node.side_effect = lambda _state: {
            "scored_recommendations": [
                passed_web,
                vector_store,
                failed_web,
            ]
        }

        await research_category(
            self.category,
            city_slug="test-city",
            city_name="Test City",
            trip_id=self.trip_id,
            trigger_reason="initial",
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
            await research_category(
                self.category,
                city_slug="test-city",
                city_name="Test City",
                trip_id=self.trip_id,
                trigger_reason="initial",
            )

        self.assertEqual(grade.call_count, 2)
        self.pool.close.assert_not_awaited()

    @patch("app.graph.research.create_embeddings", side_effect=RuntimeError("api down"))
    async def test_embedding_failure_is_visible(self, embed):
        with self.assertRaisesRegex(ResearchCategoryError, "Embedding failed"):
            await research_category(
                self.category,
                city_slug="test-city",
                city_name="Test City",
                trip_id=self.trip_id,
                trigger_reason="initial",
            )

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
                    **_tool_output([2])["grades"][0],
                    "relevance_score": 0.2,
                },
                {
                    **_tool_output([1])["grades"][0],
                    "relevance_score": 0.9,
                },
            ]
        }

        result = _grade_candidates(self.category, self.candidates)

        self.assertEqual([item.id for item in result], ["first-id", "second-id"])
        self.assertEqual([item.relevance_score for item in result], [0.9, 0.2])

    @patch("app.graph.research.call_forced_tool")
    def test_unknown_candidate_id_retries_then_raises(self, grade):
        grade.return_value = _tool_output([1, 3])

        with self.assertRaisesRegex(ResearchCategoryError, "after one retry") as raised:
            _grade_candidates(self.category, self.candidates)

        self.assertEqual(grade.call_count, 2)
        self.assertIn("unknown candidate_id", str(raised.exception.__cause__))

    @patch("app.graph.research.call_forced_tool")
    def test_missing_candidate_grade_retries_then_raises(self, grade):
        grade.return_value = _tool_output([1])

        with self.assertRaisesRegex(ResearchCategoryError, "after one retry") as raised:
            _grade_candidates(self.category, self.candidates)

        self.assertEqual(grade.call_count, 2)
        self.assertIn("2", str(raised.exception.__cause__))

    @patch("app.graph.research.call_forced_tool")
    def test_duplicate_candidate_id_retries_then_raises(self, grade):
        grade.return_value = _tool_output([1, 1])

        with self.assertRaisesRegex(ResearchCategoryError, "after one retry") as raised:
            _grade_candidates(self.category, self.candidates)

        self.assertEqual(grade.call_count, 2)
        self.assertIn("duplicate grade", str(raised.exception.__cause__))

    @patch("app.graph.research.call_forced_tool")
    def test_matches_index_not_similar_real_id(self, grade):
        candidates = [
            self.candidates[0].model_copy(update={"id": "other-id"}),
            self.candidates[1].model_copy(update={"id": "1"}),
        ]
        grade.return_value = {
            "grades": [
                {**_tool_output([1])["grades"][0], "relevance_score": 0.9},
                {**_tool_output([2])["grades"][0], "relevance_score": 0.2},
            ]
        }

        result = _grade_candidates(self.category, candidates)

        self.assertEqual([item.id for item in result], ["other-id", "1"])
        self.assertEqual([item.relevance_score for item in result], [0.9, 0.2])


class VenueExtractionTests(TestCase):
    @patch("app.graph.research.call_forced_tool")
    def test_caps_extracted_venues_at_five(self, extract):
        category = Category(name="Food", rationale="Find local institutions")
        web_results = [
            WebSearchResult(
                title=f"Venue {index}",
                url=f"https://example.com/{index}",
                content=f"Venue {index} is a distinct, well-evidenced local place.",
            )
            for index in range(12)
        ]
        extract.return_value = {
            "venues": [
                {
                    "name": f"Venue {index}",
                    "description": "A distinct, well-evidenced local place.",
                    "source_url": f"https://example.com/{index}",
                }
                for index in range(5)
            ]
        }

        result = _extract_venues(category, web_results)

        self.assertEqual(len(result), 5)
        self.assertEqual(
            extract.call_args.kwargs["tool_schema"]["input_schema"]["properties"][
                "venues"
            ]["maxItems"],
            5,
        )
        self.assertIn("at most 5 venues", extract.call_args.kwargs["system_prompt"])
        self.assertIn(
            "prioritize the most specific, well-evidenced venues",
            extract.call_args.kwargs["system_prompt"],
        )
