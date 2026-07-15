from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from app.api.routes import (
    ResumeRequest,
    SessionEntry,
    _event_generator,
    _hitl_event_from_snapshot,
    _resume_graph,
    _run_graph,
    _sessions,
    _to_sse_event,
    resume_brief,
)
from app.models.schemas import ItineraryPayload, ScoredRecommendation


def _recommendation(identifier: str = "one") -> ScoredRecommendation:
    return ScoredRecommendation(
        id=identifier,
        name="Cafe Local",
        category="Food",
        description="A neighborhood cafe.",
        source="web_search",
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

        events = [event async for event in _event_generator(session_id)]

        self.assertEqual(len(events), 1)
        self.assertTrue(entry.queue.empty())

    async def test_resumed_run_delivers_itinerary_then_terminal_sentinel(self):
        session_id = "resumed-stream"
        _sessions[session_id] = SessionEntry()
        graph = SimpleNamespace(
            astream_events=lambda *args, **kwargs: _itinerary_events()
        )

        with patch("app.api.routes._graph", graph):
            await _resume_graph(session_id, ["one"])

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
                session_id, ResumeRequest(user_selections=["one"])
            )
        create_task.call_args.args[0].close()

        self.assertEqual(response.session_id, session_id)


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
