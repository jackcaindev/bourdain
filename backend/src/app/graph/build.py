"""Construction and managed persistence for The Bourdain Brief graph."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt

from app.config import get_settings
from app.db.categories import mark_categories_selected
from app.db.trips import update_trip_status
from app.graph.city_profile_node import city_profile_node
from app.graph.itinerary import assemble_itinerary
from app.graph.reduce_graded_candidates import reduce_scored_recommendations
from app.graph.research import research_category
from app.graph.state import BriefState
from app.graph.supervisor_replan import supervisor_replan_check
from app.models.schemas import Category, CategoryListPayload, HitlPayload
from app.services.vector_store import close_shared_pool


class _ResearchInput(TypedDict):
    category: Category
    trip_id: UUID
    city_slug: str
    destination: str
    location_bias: tuple[float, float]
    trigger_reason: Literal["initial", "supervisor_replan"]


def _checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.models.schemas", model_name)
            for model_name in (
                "Candidate",
                "Category",
                "GradedCandidate",
                "ItineraryDay",
                "ItinerarySlot",
                "ScoredRecommendation",
            )
        ]
    )


async def _research_category_adapter(
    state: _ResearchInput,
) -> dict[str, Any]:
    return await research_category(
        state["category"],
        trip_id=state["trip_id"],
        trigger_reason=state["trigger_reason"],
        city_slug=state["city_slug"],
        city_name=state["destination"],
        location_bias=state["location_bias"],
    )


async def _category_select(state: BriefState) -> dict[str, list[Category]]:
    selected_names = interrupt(CategoryListPayload(categories=state["categories"]))
    if not isinstance(selected_names, list) or not all(
        isinstance(name, str) for name in selected_names
    ):
        raise ValueError("HITL resume value must be a list of category names")
    selected_categories = [
        category
        for category in state["categories"]
        if category.name in selected_names
    ]
    category_ids: list[UUID] = []
    for category in selected_categories:
        if category.id is None:
            raise ValueError(
                f"Selected category {category.name!r} has no persisted id"
            )
        category_ids.append(category.id)
    await mark_categories_selected(
        trip_id=state["trip_id"], category_ids=category_ids
    )
    await update_trip_status(state["trip_id"], "researching")
    return {"selected_categories": selected_categories}


def _dispatch_initial_research(state: BriefState) -> list[Send]:
    return [
        Send(
            "research_category",
            {
                "category": category,
                "trip_id": state["trip_id"],
                "city_slug": state["city_slug"],
                "destination": state["destination"],
                "location_bias": (
                    state["destination_lat"],
                    state["destination_lng"],
                ),
                "trigger_reason": "initial",
            },
        )
        for category in state["selected_categories"] or []
    ]


def _route_after_replan(state: BriefState) -> list[Send] | str:
    if state["replan_categories"]:
        return [
            Send(
                "research_category",
                {
                    "category": category,
                    "trip_id": state["trip_id"],
                    "city_slug": state["city_slug"],
                    "destination": state["destination"],
                    "location_bias": (
                        state["destination_lat"],
                        state["destination_lng"],
                    ),
                    "trigger_reason": "supervisor_replan",
                },
            )
            for category in state["replan_categories"]
        ]
    return "select_recommendations"


def _select_recommendations(state: BriefState) -> dict[str, list[str]]:
    selections = interrupt(
        HitlPayload(recommendations=state["scored_recommendations"])
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
    graph.add_node("city_profile_node", city_profile_node)
    graph.add_node("category_select", _category_select)
    graph.add_node("research_category", _research_category_adapter)
    graph.add_node("reduce_scored_recommendations", reduce_scored_recommendations)
    graph.add_node("supervisor_replan_check", supervisor_replan_check)
    graph.add_node("select_recommendations", _select_recommendations)
    graph.add_node("assemble_itinerary", assemble_itinerary)

    graph.add_edge(START, "city_profile_node")
    graph.add_edge("city_profile_node", "category_select")
    graph.add_conditional_edges(
        "category_select",
        _dispatch_initial_research,
        ["research_category"],
    )
    graph.add_edge("research_category", "reduce_scored_recommendations")
    graph.add_edge("reduce_scored_recommendations", "supervisor_replan_check")
    graph.add_conditional_edges(
        "supervisor_replan_check",
        _route_after_replan,
        ["research_category", "select_recommendations"],
    )
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
