from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Send

from app.graph.build import (
    _checkpoint_serializer,
    _dispatch_initial_research,
    _route_after_replan,
    build_graph,
    compile_graph,
)
from app.models.schemas import Category, ScoredRecommendation
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
    def test_initial_dispatch_sends_every_category(self):
        categories = [
            Category(name="Food", rationale="Food rationale"),
            Category(name="Markets", rationale="Market rationale"),
        ]

        sends = _dispatch_initial_research(
            {
                "selected_categories": categories,
                "city_slug": "porto",
                "destination": "Porto",
            }
        )

        self.assertTrue(all(isinstance(item, Send) for item in sends))
        self.assertEqual([item.arg["category"] for item in sends], categories)

    def test_replan_dispatches_only_replacements_or_scores(self):
        replacement = Category(name="History", rationale="History rationale")

        sends = _route_after_replan(
            {
                "replan_categories": [replacement],
                "city_slug": "porto",
                "destination": "Porto",
            }
        )
        self.assertEqual([item.arg["category"] for item in sends], [replacement])
        self.assertEqual(
            _route_after_replan({"replan_categories": []}),
            "select_recommendations",
        )


class GraphHitlTests(IsolatedAsyncioTestCase):
    async def test_replan_fanout_interrupt_and_resume_without_replay(self):
        initial_categories = [
            Category(name="Food", rationale="Food rationale"),
            Category(name="Beaches", rationale="Beach rationale"),
        ]
        replacement = Category(name="History", rationale="History rationale")
        researched: list[str] = []
        node_counts = {"city_profile": 0, "replan": 0}

        async def city_profile(state):
            node_counts["city_profile"] += 1
            return {
                "city_slug": "porto",
                "categories": initial_categories,
                "selected_categories": None,
                "research_iteration": 0,
                "replan_categories": [],
            }

        async def research(category, *, city_slug, city_name):
            researched.append(category.name)
            self.assertEqual(city_slug, "porto")
            self.assertEqual(city_name, "Porto")
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
            self.assertCountEqual(researched, ["Food", "Beaches", "History"])
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
