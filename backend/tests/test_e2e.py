"""Full-stack journey test with only third-party network boundaries mocked."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from types import MethodType
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import httpx

from app.main import app, lifespan
from app.services.places import PlaceMatch
from app.services.vector_store import get_shared_pool
import app.api.routes as routes


class _StreamingResponse(httpx.AsyncByteStream):
    """Expose ASGI body frames as they arrive and own the ASGI request task."""

    def __init__(
        self,
        queue: asyncio.Queue[bytes | None],
        app_task: asyncio.Task[None],
        disconnect: asyncio.Event,
    ) -> None:
        self._queue = queue
        self._app_task = app_task
        self._disconnect = disconnect

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                await self._app_task
                return
            yield chunk

    async def aclose(self) -> None:
        self._disconnect.set()
        if not self._app_task.done():
            self._app_task.cancel()
        await asyncio.gather(self._app_task, return_exceptions=True)


async def _streaming_asgi_request(
    transport: httpx.ASGITransport, request: httpx.Request
) -> httpx.Response:
    """A streaming equivalent of httpx 0.28's buffering ASGI handler."""

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": request.method,
        "headers": [(key.lower(), value) for key, value in request.headers.raw],
        "scheme": request.url.scheme,
        "path": request.url.path,
        "raw_path": request.url.raw_path.split(b"?")[0],
        "query_string": request.url.query,
        "server": (request.url.host, request.url.port),
        "client": transport.client,
        "root_path": transport.root_path,
    }
    request_chunks = request.stream.__aiter__()
    request_complete = False
    disconnect = asyncio.Event()
    response_started = asyncio.Event()
    response_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    response_status: int | None = None
    response_headers: list[tuple[bytes, bytes]] | None = None

    async def receive() -> dict[str, Any]:
        nonlocal request_complete
        if request_complete:
            await disconnect.wait()
            return {"type": "http.disconnect"}
        try:
            body = await request_chunks.__anext__()
            return {"type": "http.request", "body": body, "more_body": True}
        except StopAsyncIteration:
            request_complete = True
            return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_status, response_headers
        if message["type"] == "http.response.start":
            response_status = message["status"]
            response_headers = message.get("headers", [])
            response_started.set()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body and request.method != "HEAD":
                await response_queue.put(body)
            if not message.get("more_body", False):
                await response_queue.put(None)
                disconnect.set()

    async def run_app() -> None:
        try:
            await transport.app(scope, receive, send)
        finally:
            response_started.set()

    app_task = asyncio.create_task(run_app())
    await response_started.wait()
    if response_status is None or response_headers is None:
        await app_task
        raise RuntimeError("ASGI app completed without starting a response")
    return httpx.Response(
        response_status,
        headers=response_headers,
        stream=_StreamingResponse(response_queue, app_task, disconnect),
    )


async def _sse_events(
    client: httpx.AsyncClient, session_id: str
) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed SSE data envelopes from the real streaming route."""

    async with client.stream("GET", f"/api/brief/{session_id}/stream") as response:
        response.raise_for_status()
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if not line:
                if data_lines:
                    yield json.loads("\n".join(data_lines))
                    data_lines.clear()
                continue
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())


async def _wait_for_sse(
    client: httpx.AsyncClient,
    session_id: str,
    event_type: str,
    node_name: str,
) -> dict[str, Any]:
    async for event in _sse_events(client, session_id):
        if event["event_type"] == "error":
            raise AssertionError(f"graph emitted an error event: {event}")
        if (event["event_type"], event["node_name"]) == (
            event_type,
            node_name,
        ):
            return event
    raise AssertionError(f"stream ended before {(event_type, node_name)!r}")


class BriefJourneyE2ETest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session_id = f"e2e-{uuid4()}"
        self.trip_id: UUID | None = None
        self.snippet_ids = [uuid4(), uuid4(), uuid4()]
        self.venue_names = [
            "Casa Guedes Test Kitchen",
            "Conga Neighborhood Counter",
            "Gazela Local Snack Bar",
        ]

        self.call_forced_tool = Mock(side_effect=self._call_forced_tool)
        self.create_embeddings = Mock(
            side_effect=lambda texts: [[0.01] * 1536 for _ in texts]
        )
        self.search_web = Mock(return_value=[])
        self.search_text = AsyncMock(side_effect=self._search_text)

        # These modules import the service functions directly. Patch the service
        # symbols and their already-imported aliases to preserve the same network
        # boundary regardless of test discovery/import order.
        patch_targets: dict[str, Any] = {
            "app.services.llm.call_forced_tool": self.call_forced_tool,
            "app.graph.city_profile_node.call_forced_tool": self.call_forced_tool,
            "app.graph.research.call_forced_tool": self.call_forced_tool,
            "app.graph.scoring.call_forced_tool": self.call_forced_tool,
            "app.graph.guardrails.call_forced_tool": self.call_forced_tool,
            "app.graph.supervisor_replan.call_forced_tool": self.call_forced_tool,
            "app.services.embeddings.create_embeddings": self.create_embeddings,
            "app.graph.research.create_embeddings": self.create_embeddings,
            "app.services.web_search.search_web": self.search_web,
            "app.services.places.search_text": self.search_text,
        }
        self.patchers = [
            patch(target, new=value) for target, value in patch_targets.items()
        ]
        for patcher in self.patchers:
            patcher.start()

        self.lifespan_manager = lifespan(app)
        await self.lifespan_manager.__aenter__()

        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO local_guide_snippets (
                    id, name, content, category, city_slug, metadata, embedding
                )
                VALUES ($1, $2, $3, 'local food', 'porto-portugal', $4::jsonb, $5)
                """,
                [
                    (
                        snippet_id,
                        name,
                        (
                            f"{name} is a long-running Porto counter known for "
                            "specific local recipes and a regular neighborhood crowd."
                        ),
                        json.dumps({"source_url": f"https://example.test/{index}"}),
                        [0.01] * 1536,
                    )
                    for index, (snippet_id, name) in enumerate(
                        zip(self.snippet_ids, self.venue_names, strict=True), start=1
                    )
                ],
            )

        transport = httpx.ASGITransport(app=app)
        transport.handle_async_request = MethodType(  # type: ignore[method-assign]
            _streaming_asgi_request, transport
        )
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        if hasattr(self, "client"):
            await self.client.aclose()
        if hasattr(self, "lifespan_manager"):
            pool = await get_shared_pool()
            async with pool.acquire() as connection:
                if self.trip_id is not None:
                    await connection.execute(
                        "DELETE FROM trips WHERE id = $1", self.trip_id
                    )
                await connection.execute(
                    "DELETE FROM local_guide_snippets WHERE id = ANY($1::uuid[])",
                    self.snippet_ids,
                )
                await connection.execute(
                    "DELETE FROM places WHERE google_place_id LIKE $1",
                    f"e2e-place-{self.session_id}%",
                )
                for table in ("checkpoint_writes", "checkpoints", "checkpoint_blobs"):
                    await connection.execute(
                        f"DELETE FROM {table} WHERE thread_id = $1",
                        self.session_id,
                    )
            routes._sessions.pop(self.session_id, None)
            await self.lifespan_manager.__aexit__(None, None, None)
        for patcher in reversed(getattr(self, "patchers", [])):
            patcher.stop()

    def _call_forced_tool(self, **kwargs: Any) -> dict[str, Any]:
        schema_name = kwargs["tool_schema"]["name"]
        user_prompt = kwargs["user_prompt"]
        if schema_name == "derive_driver_categories":
            driver = user_prompt.split("Checked driver: ", 1)[1].splitlines()[0]
            if driver == "Local Life & Offbeat":
                names = ["Neighborhood sandwich counters", "Independent market halls"]
            else:
                names = ["Traditional Porto lunches", "Family-run snack bars"]
            return {
                "categories": [
                    {
                        "name": name,
                        "estimated_duration_minutes": 75,
                        "neighborhood_scope": "Baixa and Bonfim",
                    }
                    for name in names
                ]
            }
        if schema_name == "grade_research_candidates":
            candidates = json.loads(user_prompt.split("Candidates:\n", 1)[1])
            return {
                "grades": [
                    {
                        "candidate_id": item["candidate_id"],
                        "relevance_score": 0.94,
                        "authenticity_signal": (
                            "Specific Porto food tradition with a sustained local clientele."
                        ),
                        "confidence": "high",
                        "needs_fallback": False,
                    }
                    for item in candidates
                ]
            }
        if schema_name == "score_bourdain_candidates":
            candidates = json.loads(user_prompt.split("Candidates:\n", 1)[1])
            return {
                "scores": [
                    {
                        "candidate_id": item["candidate_id"],
                        "bourdain_score": 5,
                        "scoring_rationale": (
                            "The supplied evidence shows a specific Porto institution "
                            "with enduring neighborhood character."
                        ),
                        "locally_owned_signal": None,
                    }
                    for item in candidates
                ]
            }
        if schema_name == "check_recommendation_grounding":
            recommendations = json.loads(user_prompt.split("grounding:\n", 1)[1])
            return {
                "results": [
                    {
                        "recommendation_id": item["id"],
                        "is_grounded": True,
                        "guardrail_note": None,
                    }
                    for item in recommendations
                ]
            }
        if schema_name == "revise_research_categories":
            return {"replacements": []}
        if schema_name == "extract_venues":
            return {"venues": []}
        raise AssertionError(f"unexpected tool schema: {schema_name}")

    async def _search_text(self, query: str, **kwargs: Any) -> list[PlaceMatch]:
        if query == "Porto":
            return [
                PlaceMatch(
                    google_place_id=f"e2e-city-{self.session_id}",
                    name="Porto",
                    formatted_address="Porto, Portugal",
                    lat=41.1579,
                    lng=-8.6291,
                    google_types=["locality", "political"],
                )
            ]
        for index, name in enumerate(self.venue_names, start=1):
            if name in query:
                return [
                    PlaceMatch(
                        google_place_id=f"e2e-place-{self.session_id}-{index}",
                        name=name,
                        formatted_address=f"{index} Rua do Teste, Porto, Portugal",
                        lat=41.1579 + index / 10_000,
                        lng=-8.6291 - index / 10_000,
                        google_types=["restaurant", "food"],
                    )
                ]
        raise AssertionError(f"unexpected Places query: {query}")

    async def test_complete_brief_journey(self) -> None:
        start = await self.client.post(
            "/api/brief",
            json={
                "session_id": self.session_id,
                "destination": "Porto",
                "trip_length_days": 1,
                "activity_drivers": ["Local Life & Offbeat"],
                "food_selections": ["Lunch"],
                "time_blocks": ["morning", "afternoon", "night"],
            },
        )
        self.assertEqual(start.status_code, 200, start.text)
        self.trip_id = UUID(start.json()["trip_id"])

        category_pause = await _wait_for_sse(
            self.client, self.session_id, "hitl_pause", "category_select"
        )
        offered_categories = category_pause["payload"]["categories"]
        offered_names = [category["name"] for category in offered_categories]
        self.assertGreaterEqual(len(offered_names), 2)
        selected_category = offered_categories[0]

        category_resume = await self.client.post(
            f"/api/brief/{self.session_id}/resume",
            json={
                "resume_type": "categories",
                "user_selections": [selected_category["name"]],
            },
        )
        self.assertEqual(category_resume.status_code, 200, category_resume.text)

        venue_pause = await _wait_for_sse(
            self.client, self.session_id, "hitl_pause", "venue_select"
        )
        recommendations = venue_pause["payload"]["recommendations"]
        recommendation_ids = [item["id"] for item in recommendations]
        self.assertGreaterEqual(len(recommendation_ids), 2)

        venue_resume = await self.client.post(
            f"/api/brief/{self.session_id}/resume",
            json={
                "resume_type": "venues",
                "user_selections": [recommendation_ids[0]],
            },
        )
        self.assertEqual(venue_resume.status_code, 200, venue_resume.text)

        assembled = await _wait_for_sse(
            self.client, self.session_id, "node_complete", "assemble_itinerary"
        )
        streamed_days = assembled["payload"]["days"]
        self.assertGreaterEqual(len(streamed_days), 1)

        itinerary_response = await self.client.get(
            f"/api/trips/{self.trip_id}/itinerary"
        )
        self.assertEqual(itinerary_response.status_code, 200, itinerary_response.text)
        itinerary = itinerary_response.json()
        self.assertEqual(itinerary["trip_id"], str(self.trip_id))
        self.assertGreaterEqual(len(itinerary["days"]), 1)
        self.assertEqual(
            [day["day_number"] for day in itinerary["days"]],
            [day["day_number"] for day in streamed_days],
        )
        self.assertEqual(
            [
                [slot["time_block"] for slot in day["slots"]]
                for day in itinerary["days"]
            ],
            [[slot["time_block"] for slot in day["slots"]] for day in streamed_days],
        )

        confirmation = await self.client.patch(
            f"/api/trips/{self.trip_id}/itinerary/days/1/confirm"
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.text)
        self.assertEqual(confirmation.json()["status"], "confirmed")

        persisted_recommendations = [
            recommendation
            for day in itinerary["days"]
            for slot in day["slots"]
            for recommendation in (
                ([slot["activity"]] if slot["activity"] else []) + slot["meals"]
            )
        ]
        alternatives_by_category: dict[str, list[str]] = {}
        for recommendation in recommendations:
            alternatives_by_category.setdefault(recommendation["category"], []).append(
                recommendation["db_recommendation_id"]
            )
        occupant = next(
            recommendation
            for recommendation in persisted_recommendations
            if any(
                alternative != recommendation["id"]
                for alternative in alternatives_by_category[
                    recommendation["category_name"]
                ]
            )
        )
        replacement_id = next(
            alternative
            for alternative in alternatives_by_category[occupant["category_name"]]
            if alternative != occupant["id"]
        )
        swap = await self.client.patch(
            f"/api/trips/{self.trip_id}/itinerary/slots/{occupant['slot_id']}",
            json={"recommendation_id": replacement_id},
        )
        self.assertEqual(swap.status_code, 200, swap.text)
        self.assertEqual(swap.json()["recommendation_id"], replacement_id)

        further_research = await self.client.post(
            f"/api/trips/{self.trip_id}/categories/"
            f"{selected_category['id']}/further-research"
        )
        self.assertEqual(further_research.status_code, 200, further_research.text)
        self.assertIsInstance(further_research.json(), list)

        self.call_forced_tool.assert_called()
        self.create_embeddings.assert_called()
        self.search_text.assert_awaited()
        self.search_web.assert_not_called()
