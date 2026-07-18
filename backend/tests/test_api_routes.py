import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import (
    BriefRequest,
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
    start_brief,
)
from app.models.schemas import (
    Category,
    ItineraryDay,
    ItineraryPayload,
    ItinerarySlot,
    ScoredRecommendation,
)
from app.services.places import CityResolution, PlaceMatch


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
    def test_category_pause_includes_duration_and_eligible_blocks(self):
        category = Category(
            name="Late-night music rooms",
            estimated_duration_minutes=120,
            eligible_blocks=["night"],
        )

        event = _to_sse_event(
            {
                "event": "on_chain_end",
                "name": "category_select",
                "data": {"output": {"categories": [category]}},
            }
        )

        self.assertIsNotNone(event)
        assert event is not None and event.payload is not None
        payload = event.payload.model_dump(mode="json")
        self.assertEqual(payload["categories"][0]["estimated_duration_minutes"], 120)
        self.assertEqual(payload["categories"][0]["eligible_blocks"], ["night"])

    def test_research_category_reports_leads(self):
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
                                "slots": [
                                    {
                                        "time_block": "morning",
                                        "activity": None,
                                        "meals": [recommendation],
                                    }
                                ],
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
        self.assertEqual(event.payload.days[0].slots[0].meals[0].id, "one")

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


class StartBriefTests(IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        _sessions.clear()

    async def test_resolved_city_creates_trip_before_starting_graph(self):
        match = PlaceMatch(
            google_place_id="porto-id",
            name="Porto",
            formatted_address="Porto, Portugal",
            lat=41.1579,
            lng=-8.6291,
            google_types=["locality", "political"],
        )
        resolution = CityResolution(status="resolved", match=match)
        req = BriefRequest(
            session_id="porto-session",
            destination="Porto",
            trip_length_days=3,
            activity_drivers=["Culture & History"],
            food_selections=["Dinner"],
            time_blocks=["afternoon", "night"],
        )

        with (
            patch(
                "app.api.routes.resolve_city",
                new=AsyncMock(return_value=resolution),
            ) as resolve_city,
            patch("app.api.routes.create_trip", new_callable=AsyncMock) as create_trip,
            patch("app.api.routes.asyncio.create_task") as create_task,
        ):
            response = await start_brief(req)

        create_task.call_args.args[0].close()
        resolve_city.assert_awaited_once_with("Porto")
        create_trip.assert_awaited_once_with(
            destination_raw="Porto",
            destination_place_id="porto-id",
            destination_formatted="Porto, Portugal",
            destination_lat=41.1579,
            destination_lng=-8.6291,
            trip_length_days=3,
            activity_drivers=["Culture & History"],
            food_selections=["Dinner"],
            time_blocks=["afternoon", "night"],
            session_id="porto-session",
        )
        create_task.assert_called_once()
        self.assertEqual(response.session_id, "porto-session")
        self.assertIn("porto-session", _sessions)

    async def test_ambiguous_city_returns_candidates_without_starting_graph(self):
        candidate = PlaceMatch(
            google_place_id="springfield-id",
            name="Springfield",
            formatted_address="Springfield, Illinois, USA",
            lat=39.7817,
            lng=-89.6501,
            google_types=["locality", "political"],
        )
        resolution = CityResolution(status="ambiguous", candidates=[candidate])
        req = BriefRequest(
            session_id="springfield-session",
            destination="Springfield",
            trip_length_days=2,
            activity_drivers=["Local Life & Offbeat"],
            food_selections=["Coffee"],
            time_blocks=["morning"],
        )

        with (
            patch(
                "app.api.routes.resolve_city",
                new=AsyncMock(return_value=resolution),
            ),
            patch("app.api.routes.create_trip", new_callable=AsyncMock) as create_trip,
            patch("app.api.routes.asyncio.create_task") as create_task,
        ):
            response = await start_brief(req)

        self.assertEqual(response.status_code, 300)
        payload = json.loads(response.body)
        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["candidates"][0]["google_place_id"], "springfield-id")
        create_trip.assert_not_awaited()
        create_task.assert_not_called()
        self.assertNotIn("springfield-session", _sessions)

    def test_brief_request_rejects_unknown_checkbox_values(self):
        base = {
            "session_id": "validation-session",
            "destination": "Porto",
            "trip_length_days": 3,
            "activity_drivers": ["Nightlife"],
            "food_selections": ["Dinner"],
            "time_blocks": ["night"],
        }

        for field_name, unknown in (
            ("activity_drivers", "Museums"),
            ("food_selections", "Brunch"),
            ("time_blocks", "late-night"),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    BriefRequest(**{**base, field_name: [unknown]})


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
                slots=[
                    ItinerarySlot(
                        time_block="morning", meals=[_recommendation()]
                    )
                ],
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
                        "slots": [
                            {
                                "time_block": "morning",
                                "activity": None,
                                "meals": [_recommendation()],
                            }
                        ],
                    }
                ]
            }
        },
    }
