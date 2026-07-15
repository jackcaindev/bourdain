"""Construction and managed persistence for The Bourdain Brief graph."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt

from app.config import get_settings
from app.graph.guardrails import guardrail_node
from app.graph.itinerary import assemble_itinerary
from app.graph.reduce_graded_candidates import reduce_graded_candidates
from app.graph.research import research_category
from app.graph.scoring import scoring_node
from app.graph.state import BriefState
from app.graph.supervisor import supervisor_node
from app.graph.supervisor_replan import supervisor_replan_check
from app.models.schemas import Category
from app.services.vector_store import close_shared_pool


class _ResearchInput(TypedDict):
    category: Category


def _checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.models.schemas", model_name)
            for model_name in (
                "Candidate",
                "Category",
                "GradedCandidate",
                "ItineraryDay",
                "ScoredRecommendation",
            )
        ]
    )


def _supervisor_adapter(state: BriefState) -> dict[str, Any]:
    return supervisor_node(state) | {
        "research_iteration": 0,
        "replan_categories": [],
    }


async def _research_category_adapter(
    state: _ResearchInput,
) -> dict[str, Any]:
    return await research_category(state["category"])


def _dispatch_initial_research(state: BriefState) -> list[Send]:
    return [
        Send("research_category", {"category": category})
        for category in state["categories"]
    ]


def _route_after_replan(state: BriefState) -> list[Send] | str:
    if state["replan_categories"]:
        return [
            Send("research_category", {"category": category})
            for category in state["replan_categories"]
        ]
    return "scoring_node"


def _select_recommendations(state: BriefState) -> dict[str, list[str]]:
    selections = interrupt(
        {
            "recommendations": [
                recommendation.model_dump(mode="json")
                for recommendation in state["scored_recommendations"]
            ]
        }
    )
    if not isinstance(selections, list) or not all(
        isinstance(selection, str) for selection in selections
    ):
        raise ValueError("HITL resume value must be a list of recommendation ids")
    if len(selections) != len(set(selections)):
        raise ValueError("HITL resume value contains duplicate recommendation ids")

    valid_ids = {item.id for item in state["scored_recommendations"]}
    unknown_ids = set(selections) - valid_ids
    if unknown_ids:
        raise ValueError(
            "HITL resume value contains unknown recommendation id(s): "
            f"{', '.join(sorted(unknown_ids))}"
        )
    return {"user_selections": selections}


def compile_graph(checkpointer: BaseCheckpointSaver[Any]) -> CompiledStateGraph:
    """Compile the complete graph with a caller-owned checkpointer."""

    graph = StateGraph(BriefState)
    graph.add_node("supervisor", _supervisor_adapter)
    graph.add_node("research_category", _research_category_adapter)
    graph.add_node("reduce_graded_candidates", reduce_graded_candidates)
    graph.add_node("supervisor_replan_check", supervisor_replan_check)
    graph.add_node("scoring_node", scoring_node)
    graph.add_node("guardrail_node", guardrail_node)
    graph.add_node("select_recommendations", _select_recommendations)
    graph.add_node("assemble_itinerary", assemble_itinerary)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _dispatch_initial_research,
        ["research_category"],
    )
    graph.add_edge("research_category", "reduce_graded_candidates")
    graph.add_edge("reduce_graded_candidates", "supervisor_replan_check")
    graph.add_conditional_edges(
        "supervisor_replan_check",
        _route_after_replan,
        ["research_category", "scoring_node"],
    )
    graph.add_edge("scoring_node", "guardrail_node")
    graph.add_edge("guardrail_node", "select_recommendations")
    graph.add_edge("select_recommendations", "assemble_itinerary")
    graph.add_edge("assemble_itinerary", END)

    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def build_graph() -> AsyncIterator[CompiledStateGraph]:
    """Yield a compiled graph while its Postgres checkpointer remains open."""

    database_url = get_settings().database_url.get_secret_value()
    try:
        async with AsyncPostgresSaver.from_conn_string(
            database_url,
            serde=_checkpoint_serializer(),
        ) as checkpointer:
            await checkpointer.setup()
            yield compile_graph(checkpointer)
    finally:
        await close_shared_pool()
