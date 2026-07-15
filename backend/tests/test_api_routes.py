from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.routes import (
    ResumeRequest,
    SessionEntry,
    _event_generator,
    _hitl_event_from_snapshot,
    _resume_graph,
    _run_graph,
    _sessions,
    _to_sse_event,
    get_brief_state,
    resume_brief,
)
from app.models.schemas import (
    CacheHitPayload,
    Category,
    ItineraryDay,
    ItineraryPayload,
    ScoredRecommendation,
)


def _recommendation(
    identifier: str = "one", *, source: str = "web_search"
) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=identifier,
        name="Cafe Local",
        category="Food",
        description="A neighborhood cafe.",
        source=source,
        source_url="https://example.com/cafe",
        raw_signal="Specific local evidence.",
        relevance_score=0.9,
        authenticity_signal="Long-running local spot.",
        confidence="high",
        needs_fallback=False,
        bourdain_score=5,
        scoring_rationale="Distinctly rooted in the neighborhood.",
        locally_owned_signal="Family operated.",
        passed_guardrail=True,
        guardrail_note=None,
    )


class SSEConversionTests(TestCase):
    def test_research_category_cache_hit_carries_cache_payload(self):
        recommendation = _recommendation(source="cache")
        event = _to_sse_event(
            {
                "event": "on_chain_end",
                "name": "research_category",
                "data": {
                    "output": {
                        "scored_recommendations": [recommendation]
                    }
                },
            }
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_type, "node_complete")
        self.assertIsInstance(event.payload, CacheHitPayload)
        self.assertEqual(
            event.message,
            "Already know this one. Pulled 1 from the files.",
        )

    def test_research_category_non_cache_reports_leads(self):
        recommendation = _recommendation(source="web_search")
        event = _to_sse_event(
            {
                "event": "on_chain_end",
                "name": "research_category",
                "data": {
                    "output": {
                        "scored_recommendations": [recommendation]
                    }
                },
            }
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_type, "node_complete")
        self.assertIsNone(event.payload)
        self.assertEqual(event.message, "Got 1 lead(s) worth looking at.")

    def test_itinerary_completion_carries_days(self):
        recommendation = _recommendation()
        event = _to_sse_event(
            {
                "event": "on_chain_end",
                "name": "assemble_itinerary",
                "data": {
                    "output": {
                        "itinerary": [
                            {
                                "day_number": 1,
                                "neighborhood_focus": "Centro",
                                "breakfast": recommendation,
                                "lunch": None,
                                "dinner": None,
                                "activities": [],
                            }
                        ]
                    }
                },
            }
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertIsInstance(event.payload, ItineraryPayload)
        assert isinstance(event.payload, ItineraryPayload)
        self.assertEqual(event.payload.days[0].breakfast.id, "one")

    def test_hitl_payload_requires_selection_interrupt(self):
        recommendation = _recommendation()
        snapshot = SimpleNamespace(
            next=("select_recommendations",),
            tasks=(
                SimpleNamespace(
                    name="select_recommendations", interrupts=(object(),)
                ),
            ),
            values={"scored_recommendations": [recommendation]},
        )

        event = _hitl_event_from_snapshot(snapshot)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.payload.recommendations[0].id, "one")  # type: ignore[union-attr]


class SSEStreamTests(IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        _sessions.clear()

    async def test_initial_run_leaves_no_end_sentinel_after_pause(self):
        session_id = "paused-session"
        _sessions[session_id] = SessionEntry()
        snapshot = SimpleNamespace(
            next=("select_recommendations",),
            tasks=(
                SimpleNamespace(
                    name="select_recommendations", interrupts=(object(),)
                ),
            ),
            values={"scored_recommendations": [_recommendation()]},
        )
        graph = SimpleNamespace(
            astream_events=lambda *args, **kwargs: _empty_events(),
            aget_state=AsyncMock(return_value=snapshot),
        )

        with patch("app.api.routes._graph", graph):
            await _run_graph(session_id, "Porto", 2)

        pause_event = await _sessions[session_id].queue.get()
        self.assertEqual(pause_event.event_type, "hitl_pause")
        self.assertTrue(_sessions[session_id].queue.empty())

    async def test_generator_stops_after_pause_without_consuming_future_events(self):
        session_id = "generator-session"
        entry = SessionEntry()
        _sessions[session_id] = entry
        pause = _hitl_event_from_snapshot(
            SimpleNamespace(
                next=("select_recommendations",),
                tasks=(
                    SimpleNamespace(
                        name="select_recommendations", interrupts=(object(),)
                    ),
                ),
                values={"scored_recommendations": [_recommendation()]},
            )
        )
        await entry.queue.put(pause)
        await entry.queue.put(None)

        events = [event async for event in _event_generator(session_id)]

        self.assertEqual(len(events), 1)
        self.assertTrue(entry.queue.empty())

    async def test_resumed_run_delivers_itinerary_then_terminal_sentinel(self):
        session_id = "resumed-stream"
        _sessions[session_id] = SessionEntry()
        graph = SimpleNamespace(
            astream_events=lambda *args, **kwargs: _itinerary_events(),
            aget_state=AsyncMock(return_value=SimpleNamespace(
                next=(),
                tasks=(),
                values={},
            )),
        )

        with patch("app.api.routes._graph", graph):
            await _resume_graph(session_id, ["one"], "venues")

        completion = await _sessions[session_id].queue.get()
        terminal = await _sessions[session_id].queue.get()
        self.assertEqual(completion.node_name, "assemble_itinerary")
        self.assertIsInstance(completion.payload, ItineraryPayload)
        self.assertIsNone(terminal)

    async def test_resume_response_uses_path_session_id(self):
        session_id = "resume-session"
        _sessions[session_id] = SessionEntry()
        with patch("app.api.routes.asyncio.create_task") as create_task:
            response = await resume_brief(
                session_id, ResumeRequest(user_selections=["one"], resume_type="venues")
            )
        create_task.call_args.args[0].close()

        self.assertEqual(response.session_id, session_id)


class BriefStateTests(IsolatedAsyncioTestCase):
    async def test_unknown_session_returns_404(self):
        graph = SimpleNamespace(
            aget_state=AsyncMock(
                return_value=SimpleNamespace(values={}, next=(), tasks=())
            )
        )

        with patch("app.api.routes._graph", graph):
            with self.assertRaises(HTTPException) as raised:
                await get_brief_state("unknown")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Session not found")

    async def test_category_select_returns_categories(self):
        categories = [Category(name="Food", rationale="Follow local rituals.")]
        snapshot = SimpleNamespace(
            next=("category_select",),
            tasks=(
                SimpleNamespace(name="category_select", interrupts=(object(),)),
            ),
            values={"categories": categories, "selected_categories": None},
        )
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))

        with patch("app.api.routes._graph", graph):
            response = await get_brief_state("category-session")

        self.assertEqual(response.phase, "category_select")
        self.assertEqual(response.categories, categories)
        self.assertIsNone(response.selected_categories)

    async def test_venue_select_returns_recommendations(self):
        selected = Category(name="Food", rationale="Follow local rituals.")
        recommendations = [_recommendation()]
        snapshot = SimpleNamespace(
            next=("select_recommendations",),
            tasks=(
                SimpleNamespace(
                    name="select_recommendations", interrupts=(object(),)
                ),
            ),
            values={
                "selected_categories": [selected],
                "scored_recommendations": recommendations,
            },
        )
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))

        with patch("app.api.routes._graph", graph):
            response = await get_brief_state("venue-session")

        self.assertEqual(response.phase, "venue_select")
        self.assertEqual(response.selected_categories, ["Food"])
        self.assertEqual(response.recommendations, recommendations)

    async def test_completed_session_returns_itinerary(self):
        itinerary = [
            ItineraryDay(
                day_number=1,
                neighborhood_focus="Centro",
                breakfast=_recommendation(),
                lunch=None,
                dinner=None,
                activities=[],
            )
        ]
        snapshot = SimpleNamespace(
            next=(),
            tasks=(),
            values={"itinerary": itinerary},
        )
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))

        with patch("app.api.routes._graph", graph):
            response = await get_brief_state("completed-session")

        self.assertEqual(response.phase, "itinerary")
        self.assertEqual(response.itinerary_days, itinerary)


async def _empty_events():
    if False:
        yield None


async def _itinerary_events():
    yield {
        "event": "on_chain_end",
        "name": "assemble_itinerary",
        "data": {
            "output": {
                "itinerary": [
                    {
                        "day_number": 1,
                        "neighborhood_focus": "Centro",
                        "breakfast": _recommendation(),
                        "lunch": None,
                        "dinner": None,
                        "activities": [],
                    }
                ]
            }
        },
    }
