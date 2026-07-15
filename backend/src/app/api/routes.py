from dataclasses import dataclass, field
import asyncio
import time
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from app.models.schemas import (
    CacheHitPayload,
    Category,
    CategoryListPayload,
    ErrorPayload,
    HitlPayload,
    ItineraryDay,
    ItineraryPayload,
    ScoredRecommendation,
    SSEEvent,
)
from langgraph.types import Command
from langgraph.graph.state import CompiledStateGraph


logger = logging.getLogger(__name__)
router = APIRouter()


@dataclass
class SessionEntry:
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)

_sessions: dict[str, SessionEntry] = {}


_graph: CompiledStateGraph | None = None


def set_graph(graph: CompiledStateGraph) -> None:
    global _graph
    _graph = graph


class BriefRequest(BaseModel):
    session_id: str
    destination: str
    trip_length_days: int


class ResumeRequest(BaseModel):
    user_selections: list[str]
    resume_type: Literal["categories", "venues"]


class SessionResponse(BaseModel):
    session_id: str


class BriefStateResponse(BaseModel):
    session_id: str
    phase: Literal[
        "category_select", "venue_select", "itinerary", "in_progress"
    ]
    categories: list[Category] | None = None
    selected_categories: list[str] | None = None
    recommendations: list[ScoredRecommendation] | None = None
    itinerary_days: list[ItineraryDay] | None = None


_SURFACED_NODES = {
    "city_profile_node",
    "category_select",
    "research_category",
    "reduce_scored_recommendations",
    "supervisor_replan_check",
    "select_recommendations",
    "assemble_itinerary",
}

_START_MESSAGES: dict[str, str] = {
    "city_profile_node": "Figuring out what this place actually is…",
    "category_select": "Here's what we can dig into.",
    "research_category": "Asking around…",
    "reduce_scored_recommendations": "Cutting the tourist traps…",
    "supervisor_replan_check": "Something's off. Looking again…",
    "select_recommendations": "Here's what we found.",
    "assemble_itinerary": "Building your days…",
}

_COMPLETE_MESSAGES: dict[str, str] = {
    "city_profile_node": "Know what we're dealing with.",
    "reduce_scored_recommendations": "Got a shortlist worth trusting.",
    "supervisor_replan_check": "Good enough. Moving on.",
    "assemble_itinerary": "That's your trip.",
}


def _to_sse_event(raw: dict) -> SSEEvent | None:
    event = raw.get("event")
    name = raw.get("name", "")
    data = raw.get("data", {})

    if event == "on_chain_start" and name in _SURFACED_NODES:
        return SSEEvent(
            event_type="node_start",
            node_name=name,
            message=_START_MESSAGES[name],
        )

    if event == "on_chain_end" and name in _SURFACED_NODES:
        # HITL 1 — category selection pause
        if name == "category_select":
            output = data.get("output", {})
            categories = output.get("categories", [])
            return SSEEvent(
                event_type="hitl_pause",
                node_name="category_select",
                message="Here's what we can dig into. Pick your lanes.",
                payload=CategoryListPayload(categories=categories),
            )

        # HITL 2 — venue selection pause
        if name == "select_recommendations":
            output = data.get("output", {})
            recommendations = output.get("scored_recommendations", [])
            return SSEEvent(
                event_type="hitl_pause",
                node_name="venue_select",
                message="Here's what we found. Pick what goes in your brief.",
                payload=HitlPayload(recommendations=recommendations),
            )

        # Itinerary assembly complete — carry days in payload
        if name == "assemble_itinerary":
            output = data.get("output", {})
            days = output.get("itinerary", [])
            return SSEEvent(
                event_type="node_complete",
                node_name=name,
                message="That's your trip.",
                payload=ItineraryPayload(days=days),
            )

        # supervisor_replan_check — dual message based on whether replan occurred
        if name == "supervisor_replan_check":
            output = data.get("output", {})
            replan_categories = output.get("replan_categories", [])
            message = (
                "Took another look. Better."
                if replan_categories
                else "Good enough. Moving on."
            )
            return SSEEvent(
                event_type="node_complete",
                node_name=name,
                message=message,
            )

        # research_category — carry cache hit payload if served from cache
        if name == "research_category":
            output = data.get("output", {})
            recs = output.get("scored_recommendations", [])
            source = recs[0].source if recs else None
            if source == "cache":
                return SSEEvent(
                    event_type="node_complete",
                    node_name=name,
                    message=f"Already know this one. Pulled {len(recs)} from the files.",
                    payload=CacheHitPayload(
                        category=recs[0].category,
                        recommendations_count=len(recs),
                    ),
                )
            return SSEEvent(
                event_type="node_complete",
                node_name=name,
                message=f"Got {len(recs)} lead(s) worth looking at.",
            )

        # Generic completion for all other surfaced nodes
        return SSEEvent(
            event_type="node_complete",
            node_name=name,
            message=_COMPLETE_MESSAGES.get(name, "Done."),
        )

    return None


def _hitl_event_from_snapshot(snapshot: Any) -> SSEEvent | None:
    """Build a pause event when astream_events omits an interrupted node end."""

    interrupted_nodes = {task.name for task in snapshot.tasks if task.interrupts}
    if (
        "category_select" in snapshot.next
        and "category_select" in interrupted_nodes
    ):
        return SSEEvent(
            event_type="hitl_pause",
            node_name="category_select",
            message="Here's what we can dig into. Pick your lanes.",
            payload=CategoryListPayload(
                categories=snapshot.values.get("categories", [])
            ),
        )

    if (
        "select_recommendations" in snapshot.next
        and "select_recommendations" in interrupted_nodes
    ):
        return SSEEvent(
            event_type="hitl_pause",
            node_name="venue_select",
            message="Here's what we found. Pick what goes in your brief.",
            payload=HitlPayload(
                recommendations=snapshot.values.get("scored_recommendations", [])
            ),
        )

    return None


async def _run_graph(session_id: str, destination: str, trip_length_days: int):
    entry = _sessions.get(session_id)
    if not entry:
        return
    should_close_stream = False
    try:
        if _graph is None:
            raise RuntimeError("Graph not initialized — lifespan not complete")
        config = {"configurable": {"thread_id": session_id}}
        inputs = {"destination": destination, "trip_length_days": trip_length_days}
        pause_emitted = False
        async for raw in _graph.astream_events(inputs, config=config, version="v2"):
            sse = _to_sse_event(raw)
            if sse:
                await entry.queue.put(sse)
                pause_emitted = pause_emitted or sse.event_type == "hitl_pause"
        snapshot = await _graph.aget_state(config)
        pause_event = _hitl_event_from_snapshot(snapshot)
        if pause_event is None:
            if snapshot.next:
                raise RuntimeError(
                    "Graph stopped without reaching an interrupt or END"
                )
            should_close_stream = True
            return
        if not pause_emitted:
            await entry.queue.put(pause_event)
    except Exception as e:
        logger.exception("Graph error for session %s", session_id)
        err = SSEEvent(
            event_type="error",
            node_name="graph",
            message=str(e),
            payload=ErrorPayload(node_name="graph", detail=str(e)),
        )
        await entry.queue.put(err)
        should_close_stream = True
    finally:
        if should_close_stream:
            await entry.queue.put(None)


async def _resume_graph(
    session_id: str,
    user_selections: list[str],
    resume_type: Literal["categories", "venues"],
):
    entry = _sessions.get(session_id)
    if not entry:
        return
    should_close_stream = False
    try:
        if _graph is None:
            raise RuntimeError("Graph not initialized — lifespan not complete")
        config = {"configurable": {"thread_id": session_id}}
        pause_emitted = False
        async for raw in _graph.astream_events(
            Command(resume=user_selections), config=config, version="v2"
        ):
            sse = _to_sse_event(raw)
            if sse:
                resumed_node = {
                    "categories": "category_select",
                    "venues": "venue_select",
                }.get(resume_type)
                if sse.event_type == "hitl_pause" and sse.node_name == resumed_node:
                    continue
                await entry.queue.put(sse)
                pause_emitted = pause_emitted or sse.event_type == "hitl_pause"
        snapshot = await _graph.aget_state(config)
        pause_event = _hitl_event_from_snapshot(snapshot)
        if pause_event is not None:
            if not pause_emitted:
                await entry.queue.put(pause_event)
            return
        if snapshot.next:
            raise RuntimeError("Graph stopped without reaching an interrupt or END")
        should_close_stream = True
    except Exception as e:
        logger.exception("Resume error for session %s", session_id)
        err = SSEEvent(
            event_type="error",
            node_name="graph",
            message=str(e),
            payload=ErrorPayload(node_name="graph", detail=str(e)),
        )
        await entry.queue.put(err)
        should_close_stream = True
    finally:
        if should_close_stream:
            await entry.queue.put(None)


async def _event_generator(session_id: str):
    entry = _sessions.get(session_id)
    if not entry:
        return
    while True:
        event: SSEEvent | None = await entry.queue.get()
        if event is None:
            break
        yield ServerSentEvent(
            data=event.model_dump_json(),
            event=event.event_type,
        )


@router.post("/brief", response_model=SessionResponse)
async def start_brief(req: BriefRequest):
    if req.session_id in _sessions:
        raise HTTPException(status_code=409, detail="Session already exists")
    _sessions[req.session_id] = SessionEntry()
    asyncio.create_task(_run_graph(req.session_id, req.destination, req.trip_length_days))
    logger.info("Brief started session=%s destination=%s", req.session_id, req.destination)
    return SessionResponse(session_id=req.session_id)


@router.get("/brief/{session_id}/stream")
async def stream_brief(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    _sessions[session_id].last_active = time.monotonic()
    return EventSourceResponse(_event_generator(session_id))


@router.get("/brief/{session_id}/state", response_model=BriefStateResponse)
async def get_brief_state(session_id: str):
    if _graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialized")

    config = {"configurable": {"thread_id": session_id}}
    snapshot = await _graph.aget_state(config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="Session not found")

    selected_categories = snapshot.values.get("selected_categories")
    selected_category_names = (
        [
            category if isinstance(category, str) else category.name
            for category in selected_categories
        ]
        if selected_categories is not None
        else None
    )
    pause_event = _hitl_event_from_snapshot(snapshot)
    if pause_event is not None and pause_event.node_name == "category_select":
        return BriefStateResponse(
            session_id=session_id,
            phase="category_select",
            categories=snapshot.values.get("categories"),
            selected_categories=selected_category_names,
        )
    if pause_event is not None and pause_event.node_name == "venue_select":
        return BriefStateResponse(
            session_id=session_id,
            phase="venue_select",
            selected_categories=selected_category_names,
            recommendations=snapshot.values.get("scored_recommendations"),
        )

    itinerary_days = snapshot.values.get("itinerary")
    if itinerary_days:
        return BriefStateResponse(
            session_id=session_id,
            phase="itinerary",
            itinerary_days=itinerary_days,
        )
    return BriefStateResponse(session_id=session_id, phase="in_progress")


@router.post("/brief/{session_id}/resume", response_model=SessionResponse)
async def resume_brief(session_id: str, req: ResumeRequest):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    _sessions[session_id].last_active = time.monotonic()
    asyncio.create_task(
        _resume_graph(session_id, req.user_selections, req.resume_type)
    )
    logger.info(
        "Brief resumed session=%s type=%s selections=%s",
        session_id,
        req.resume_type,
        req.user_selections,
    )
    return SessionResponse(session_id=session_id)


@router.get("/health")
async def health():
    return {"status": "ok"}
