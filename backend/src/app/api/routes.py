from dataclasses import dataclass, field
import asyncio
import time
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from app.models.schemas import (
    CandidatePayload,
    ErrorPayload,
    HitlPayload,
    ItineraryPayload,
    SSEEvent,
    ScorePayload,
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

class SessionResponse(BaseModel):
    session_id: str

_SURFACED_NODES = {
    "supervisor",
    "research_category",
    "reduce_graded_candidates",
    "supervisor_replan_check",
    "scoring_node",
    "guardrail_node",
    "select_recommendations",
    "assemble_itinerary",
}

_START_MESSAGES: dict[str, str] = {
    "supervisor": "Analyzing destination and selecting research categories…",
    "research_category": "Researching candidates…",
    "reduce_graded_candidates": "Consolidating research results…",
    "supervisor_replan_check": "Reviewing category coverage…",
    "scoring_node": "Scoring recommendations against the Bourdain rubric…",
    "guardrail_node": "Running quality checks…",
    "select_recommendations": "Preparing recommendations for review…",
    "assemble_itinerary": "Assembling your itinerary…",
}

_COMPLETE_MESSAGES: dict[str, str] = {
    "supervisor": "Categories selected.",
    "reduce_graded_candidates": "Research consolidated.",
    "supervisor_replan_check": "Category review complete.",
    "guardrail_node": "Quality checks complete.",
    "assemble_itinerary": "Itinerary ready.",
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
        if name == "select_recommendations":
            return None

        if name == "scoring_node":
            output = data.get("output", {})
            recs = output.get("scored_recommendations", [])
            payload = ScorePayload(recommendation=recs[0]) if recs else None
            return SSEEvent(
                event_type="node_complete",
                node_name=name,
                message=f"Scored {len(recs)} recommendation(s).",
                payload=payload,
            )

        if name == "research_category":
            output = data.get("output", {})
            candidates = output.get("candidates", [])
            first_candidate = candidates[0] if candidates else None
            if isinstance(first_candidate, dict):
                category = first_candidate.get("category", "unknown")
            else:
                category = getattr(first_candidate, "category", "unknown")
            return SSEEvent(
                event_type="node_complete",
                node_name=name,
                message=f"Found {len(candidates)} candidate(s) for {category}.",
                payload=CandidatePayload(
                    category=category,
                    candidates_found=len(candidates),
                ),
            )

        if name == "assemble_itinerary":
            output = data.get("output", {})
            return SSEEvent(
                event_type="node_complete",
                node_name=name,
                message=_COMPLETE_MESSAGES[name],
                payload=ItineraryPayload(days=output.get("itinerary", [])),
            )

        return SSEEvent(
            event_type="node_complete",
            node_name=name,
            message=_COMPLETE_MESSAGES[name],
        )

    return None


def _hitl_event_from_snapshot(snapshot: Any) -> SSEEvent | None:
    """Build a pause event only for the persisted selection interrupt."""

    if "select_recommendations" not in snapshot.next:
        return None

    interrupted = any(
        task.name == "select_recommendations" and task.interrupts
        for task in snapshot.tasks
    )
    if not interrupted:
        return None

    return SSEEvent(
        event_type="hitl_pause",
        node_name="select_recommendations",
        message="Recommendations ready — select what to include in your itinerary.",
        payload=HitlPayload(
            recommendations=snapshot.values.get("scored_recommendations", [])
        ),
    )


async def _run_graph(session_id: str, destination: str, trip_length_days: int):
    entry = _sessions.get(session_id)
    if not entry:
        return
    try:
        if _graph is None:
            raise RuntimeError("Graph not initialized — lifespan not complete")
        config = {"configurable": {"thread_id": session_id}}
        inputs = {"destination": destination, "trip_length_days": trip_length_days}
        async for raw in _graph.astream_events(inputs, config=config, version="v2"):
            sse = _to_sse_event(raw)
            if sse:
                await entry.queue.put(sse)
        snapshot = await _graph.aget_state(config)
        pause_event = _hitl_event_from_snapshot(snapshot)
        if pause_event is None:
            raise RuntimeError("Graph stopped without reaching the selection interrupt")
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
        await entry.queue.put(None)

async def _resume_graph(session_id: str, user_selections: list[str]):
    entry = _sessions.get(session_id)
    if not entry:
        return
    try:
        if _graph is None:
            raise RuntimeError("Graph not initialized — lifespan not complete")
        config = {"configurable": {"thread_id": session_id}}
        async for raw in _graph.astream_events(
            Command(resume=user_selections), config=config, version="v2"
        ):
            sse = _to_sse_event(raw)
            if sse:
                await entry.queue.put(sse)
    except Exception as e:
        logger.exception("Resume error for session %s", session_id)
        err = SSEEvent(
            event_type="error",
            node_name="graph",
            message=str(e),
            payload=ErrorPayload(node_name="graph", detail=str(e)),
        )
        await entry.queue.put(err)
    finally:
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
        if event.event_type == "hitl_pause":
            break

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

@router.post("/brief/{session_id}/resume", response_model=SessionResponse)
async def resume_brief(session_id: str, req: ResumeRequest):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    _sessions[session_id].last_active = time.monotonic()
    asyncio.create_task(_resume_graph(session_id, req.user_selections))
    logger.info("Brief resumed session=%s selections=%s", session_id, req.user_selections)
    return SessionResponse(session_id=session_id)

@router.get("/health")
async def health():
    return {"status": "ok"}
