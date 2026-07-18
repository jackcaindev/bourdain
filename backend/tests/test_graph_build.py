from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Send

from app.graph.build import (
    _checkpoint_serializer,
    _dispatch_initial_research,
    _route_after_replan,
    build_graph,
    compile_graph,
)
from app.models.schemas import (
    Category,
    ItineraryDay,
    ItinerarySlot,
    ScoredRecommendation,
)
from app.api.routes import _hitl_event_from_snapshot


def _scored(category: str) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=f"{category}-id",
        name=f"{category} place",
        category=category,
        description="Description",
        source="vector_store",
        raw_signal="Specific local evidence long enough for review.",
        relevance_score=0.8,
        authenticity_signal="Locally grounded",
        confidence="high",
        needs_fallback=False,
        bourdain_score=4,
        scoring_rationale="Grounded local recommendation.",
        locally_owned_signal=None,
        passed_guardrail=True,
        guardrail_note=None,
    )


class GraphRoutingTests(TestCase):
    def test_checkpoint_serializer_round_trips_nested_itinerary_slot(self):
        serializer = _checkpoint_serializer()
        itinerary = [
            ItineraryDay(
                day_number=1,
                slots=[ItinerarySlot(time_block="morning", activity=_scored("Food"))],
            )
        ]

        restored = serializer.loads_typed(
            serializer.dumps_typed({"itinerary": itinerary})
        )

        self.assertIsInstance(restored["itinerary"][0], ItineraryDay)
        self.assertIsInstance(restored["itinerary"][0].slots[0], ItinerarySlot)

    def test_initial_dispatch_sends_every_category(self):
        expected_trip_id = uuid4()
        categories = [
            Category(id=uuid4(), name="Food", rationale="Food rationale"),
            Category(id=uuid4(), name="Markets", rationale="Market rationale"),
        ]

        sends = _dispatch_initial_research(
            {
                "selected_categories": categories,
                "trip_id": expected_trip_id,
                "city_slug": "porto",
                "destination": "Porto",
                "destination_lat": 41.1579,
                "destination_lng": -8.6291,
            }
        )

        self.assertTrue(all(isinstance(item, Send) for item in sends))
        self.assertEqual([item.arg["category"] for item in sends], categories)
        self.assertEqual(
            [item.arg["trip_id"] for item in sends],
            [expected_trip_id, expected_trip_id],
        )
        self.assertEqual(
            [item.arg["trigger_reason"] for item in sends], ["initial", "initial"]
        )
        self.assertEqual(
            [item.arg["location_bias"] for item in sends],
            [(41.1579, -8.6291), (41.1579, -8.6291)],
        )

    def test_replan_dispatches_only_replacements_or_scores(self):
        trip_id = uuid4()
        replacement = Category(
            id=uuid4(), name="History", rationale="History rationale"
        )

        sends = _route_after_replan(
            {
                "replan_categories": [replacement],
                "trip_id": trip_id,
                "city_slug": "porto",
                "destination": "Porto",
                "destination_lat": 41.1579,
                "destination_lng": -8.6291,
            }
        )
        self.assertEqual([item.arg["category"] for item in sends], [replacement])
        self.assertEqual(sends[0].arg["trip_id"], trip_id)
        self.assertEqual(sends[0].arg["trigger_reason"], "supervisor_replan")
        self.assertEqual(sends[0].arg["location_bias"], (41.1579, -8.6291))
        self.assertEqual(
            _route_after_replan({"replan_categories": []}),
            "select_recommendations",
        )


class GraphHitlTests(IsolatedAsyncioTestCase):
    async def test_replan_fanout_interrupt_and_resume_without_replay(self):
        expected_trip_id = uuid4()
        initial_categories = [
            Category(id=uuid4(), name="Food", rationale="Food rationale"),
            Category(id=uuid4(), name="Beaches", rationale="Beach rationale"),
        ]
        replacement = Category(
            id=uuid4(), name="History", rationale="History rationale"
        )
        researched: list[tuple[str, str]] = []
        node_counts = {"city_profile": 0, "replan": 0}

        async def city_profile(state):
            node_counts["city_profile"] += 1
            return {
                "trip_id": expected_trip_id,
                "city_slug": "porto",
                "destination_lat": 41.1579,
                "destination_lng": -8.6291,
                "time_blocks": ["morning", "afternoon", "night"],
                "categories": initial_categories,
                "selected_categories": None,
                "research_iteration": 0,
                "replan_categories": [],
            }

        async def research(
            category,
            *,
            trip_id: object,
            trigger_reason: str,
            city_slug,
            city_name,
            location_bias,
        ):
            self.assertEqual(trip_id, expected_trip_id)
            researched.append((category.name, trigger_reason))
            self.assertEqual(city_slug, "porto")
            self.assertEqual(city_name, "Porto")
            self.assertEqual(location_bias, (41.1579, -8.6291))
            return {"scored_recommendations": [_scored(category.name)]}

        def replan(state):
            node_counts["replan"] += 1
            if state["research_iteration"] == 0:
                return {
                    "categories": [initial_categories[0], replacement],
                    "replan_categories": [replacement],
                    "research_iteration": 1,
                }
            return {"replan_categories": []}

        with (
            patch(
                "app.graph.build.city_profile_node",
                new=AsyncMock(side_effect=city_profile),
            ),
            patch(
                "app.graph.build.research_category",
                new=AsyncMock(side_effect=research),
            ),
            patch("app.graph.build.supervisor_replan_check", side_effect=replan),
            patch(
                "app.graph.build.mark_categories_selected",
                new_callable=AsyncMock,
            ) as mark_selected,
            patch(
                "app.graph.build.update_trip_status",
                new_callable=AsyncMock,
            ) as update_status,
        ):
            graph = compile_graph(InMemorySaver(serde=_checkpoint_serializer()))
            config = {"configurable": {"thread_id": "brief-hitl-test"}}
            category_pause = await graph.ainvoke(
                {"destination": "Porto", "trip_length_days": 2}, config=config
            )
            self.assertIn("__interrupt__", category_pause)

            paused = await graph.ainvoke(
                Command(resume=["Food", "Beaches"]), config=config
            )
            self.assertIn("__interrupt__", paused)
            pause_event = _hitl_event_from_snapshot(await graph.aget_state(config))
            self.assertIsNotNone(pause_event)
            assert pause_event is not None
            self.assertEqual(pause_event.event_type, "hitl_pause")
            self.assertEqual(
                len(pause_event.payload.recommendations), 2  # type: ignore[union-attr]
            )
            self.assertCountEqual(
                researched,
                [
                    ("Food", "initial"),
                    ("Beaches", "initial"),
                    ("History", "supervisor_replan"),
                ],
            )
            mark_selected.assert_awaited_once_with(
                trip_id=expected_trip_id,
                category_ids=[category.id for category in initial_categories],
            )
            update_status.assert_awaited_once_with(expected_trip_id, "researching")
            counts_at_pause = node_counts.copy()

            final = await graph.ainvoke(
                Command(resume=["Food-id", "History-id"]), config=config
            )

        self.assertEqual(node_counts, counts_at_pause)
        self.assertEqual(final["user_selections"], ["Food-id", "History-id"])
        self.assertEqual(
            [item.id for item in final["scored_recommendations"]],
            ["Food-id", "History-id"],
        )
        self.assertEqual(len(final["itinerary"]), 2)


class GraphLifecycleTests(IsolatedAsyncioTestCase):
    async def test_build_graph_closes_shared_pool_after_checkpointer(self):
        checkpointer = AsyncMock()
        checkpointer_context = AsyncMock()
        checkpointer_context.__aenter__.return_value = checkpointer
        compiled_graph = object()
        settings = MagicMock()
        settings.database_url.get_secret_value.return_value = "postgresql://test"

        with (
            patch("app.graph.build.get_settings", return_value=settings),
            patch(
                "app.graph.build.AsyncPostgresSaver.from_conn_string",
                return_value=checkpointer_context,
            ),
            patch("app.graph.build.compile_graph", return_value=compiled_graph),
            patch(
                "app.graph.build.close_shared_pool", new_callable=AsyncMock
            ) as close_shared_pool,
        ):
            async with build_graph() as graph:
                self.assertIs(graph, compiled_graph)
                close_shared_pool.assert_not_awaited()

        checkpointer.setup.assert_awaited_once()
        checkpointer_context.__aexit__.assert_awaited_once()
        close_shared_pool.assert_awaited_once()

    async def test_build_graph_closes_shared_pool_when_graph_use_raises(self):
        checkpointer = AsyncMock()
        checkpointer_context = AsyncMock()
        checkpointer_context.__aenter__.return_value = checkpointer
        settings = MagicMock()
        settings.database_url.get_secret_value.return_value = "postgresql://test"

        with (
            patch("app.graph.build.get_settings", return_value=settings),
            patch(
                "app.graph.build.AsyncPostgresSaver.from_conn_string",
                return_value=checkpointer_context,
            ),
            patch("app.graph.build.compile_graph", return_value=object()),
            patch(
                "app.graph.build.close_shared_pool", new_callable=AsyncMock
            ) as close_shared_pool,
        ):
            with self.assertRaisesRegex(RuntimeError, "graph failed"):
                async with build_graph():
                    raise RuntimeError("graph failed")

        checkpointer_context.__aexit__.assert_awaited_once()
        close_shared_pool.assert_awaited_once()
