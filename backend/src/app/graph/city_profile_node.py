"""Derive and persist checkbox-driven research categories for a trip."""

import asyncio
import logging
from typing import Annotated, Any, Literal
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, TypeAdapter

from app.db.categories import create_category
from app.db.trips import get_trip_by_session_id
from app.graph.state import BriefState
from app.models.domain import CategoryRecord, TripRecord
from app.models.schemas import Category, TimeBlock
from app.services.city_profiles import normalize_city_slug
from app.services.llm import call_forced_tool


logger = logging.getLogger(__name__)

CITY_PROFILE_MODEL = "claude-haiku-4-5"

ACTIVITY_BLOCK_AFFINITY: dict[str, list[TimeBlock]] = {
    "Nightlife": ["night"],
    "Arts & Music": ["afternoon", "night"],
    "Culture & History": ["morning", "afternoon"],
    "Outdoors & Nature": ["morning", "afternoon"],
    "Shopping & Markets": ["morning", "afternoon"],
    "Local Life & Offbeat": ["morning", "afternoon", "night"],
}
MEAL_BLOCK_AFFINITY: dict[str, list[TimeBlock]] = {
    "Breakfast": ["morning"],
    "Coffee": ["morning", "afternoon"],
    "Lunch": ["afternoon"],
    "Tea": ["afternoon"],
    "Dinner": ["night"],
}


class _DerivedCategory(BaseModel):
    name: str
    estimated_duration_minutes: int = Field(gt=0)
    neighborhood_scope: str


_DERIVED_CATEGORIES_ADAPTER = TypeAdapter(
    Annotated[list[_DerivedCategory], Field(min_length=2, max_length=3)]
)


def _category_tool_schema() -> dict[str, Any]:
    return {
        "name": "derive_driver_categories",
        "description": "Derive candidate research categories for one checked driver.",
        "input_schema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "estimated_duration_minutes": {
                                "type": "integer",
                                "minimum": 1,
                            },
                            "neighborhood_scope": {"type": "string"},
                        },
                        "required": [
                            "name",
                            "estimated_duration_minutes",
                            "neighborhood_scope",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["categories"],
            "additionalProperties": False,
        },
    }


def _system_prompt(category_type: Literal["food", "activity"]) -> str:
    return (
        "You derive authentic, destination-specific candidate research lanes for "
        "The Bourdain Brief. Work on exactly one checked "
        f"{category_type} driver. Return 2-3 distinct, non-touristy categories. "
        "Estimate a realistic duration for each and provide neighborhood steering "
        "text, not a claimed geographic boundary. Do not choose time blocks; those "
        "are assigned deterministically by the application. Use the tool once."
    )


def _user_prompt(trip: TripRecord, source_driver: str) -> str:
    return (
        f"Destination: {trip.destination_formatted}\n"
        f"Trip length: {trip.trip_length_days} day(s)\n"
        f"Checked driver: {source_driver}\n"
        f"Traveler-selected time blocks: {', '.join(trip.time_blocks) or 'none'}\n\n"
        "Derive 2-3 candidate categories specifically for this one checked driver."
    )


def _derive_categories_for_driver(
    trip: TripRecord,
    source_driver: str,
    category_type: Literal["food", "activity"],
) -> list[_DerivedCategory]:
    tool_input = call_forced_tool(
        system_prompt=_system_prompt(category_type),
        user_prompt=_user_prompt(trip, source_driver),
        tool_schema=_category_tool_schema(),
        model=CITY_PROFILE_MODEL,
        max_tokens=900,
    )
    return _DERIVED_CATEGORIES_ADAPTER.validate_python(tool_input["categories"])


def _to_category(record: CategoryRecord) -> Category:
    return Category(
        id=record.id,
        name=record.name,
        rationale=record.neighborhood_scope,
        type=record.type,
        source_drivers=record.source_drivers,
        estimated_duration_minutes=record.estimated_duration_minutes,
        neighborhood_scope=record.neighborhood_scope,
        eligible_blocks=record.eligible_blocks,
        status=record.status,
    )


async def _persist_derived_categories(
    trip: TripRecord,
    source_driver: str,
    category_type: Literal["food", "activity"],
    derived_categories: list[_DerivedCategory],
) -> list[Category]:
    affinity = (
        ACTIVITY_BLOCK_AFFINITY
        if category_type == "activity"
        else MEAL_BLOCK_AFFINITY
    )
    eligible_blocks = affinity[source_driver]
    persisted: list[Category] = []
    for derived in derived_categories:
        record = await create_category(
            trip_id=trip.id,
            name=derived.name,
            type=category_type,
            source_drivers=[source_driver],
            eligible_blocks=eligible_blocks,
            estimated_duration_minutes=derived.estimated_duration_minutes,
            neighborhood_scope=derived.neighborhood_scope,
            status="candidate",
        )
        persisted.append(_to_category(record))
    return persisted


def _session_id(config: RunnableConfig) -> str:
    thread_id = config.get("configurable", {}).get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("city_profile_node requires a string thread_id")
    return thread_id


async def city_profile_node(
    state: BriefState,
    config: RunnableConfig,
) -> dict[str, UUID | str | float | int | list[Category] | None]:
    """Derive 2-3 candidates per selected driver and persist them for HITL 1."""

    session_id = _session_id(config)
    trip = await get_trip_by_session_id(session_id)
    if trip is None:
        raise ValueError(f"No trip found for session {session_id!r}")

    driver_specs = [
        *((driver, "activity") for driver in trip.activity_drivers),
        *((meal, "food") for meal in trip.food_selections),
    ]
    derived_groups = await asyncio.gather(
        *(
            asyncio.to_thread(
                _derive_categories_for_driver,
                trip,
                source_driver,
                category_type,
            )
            for source_driver, category_type in driver_specs
        )
    )

    persisted_groups = await asyncio.gather(
        *(
            _persist_derived_categories(
                trip,
                source_driver,
                category_type,
                derived_categories,
            )
            for (source_driver, category_type), derived_categories in zip(
                driver_specs, derived_groups, strict=True
            )
        )
    )
    categories = [category for group in persisted_groups for category in group]
    city_slug = normalize_city_slug(trip.destination_formatted)

    logger.info(
        "city_profile_node_complete",
        extra={
            "destination": trip.destination_formatted,
            "city_slug": city_slug,
            "driver_count": len(driver_specs),
            "category_count": len(categories),
        },
    )
    return {
        "trip_id": trip.id,
        "city_slug": city_slug,
        "destination_lat": trip.destination_lat,
        "destination_lng": trip.destination_lng,
        "time_blocks": trip.time_blocks,
        "categories": categories,
        "research_iteration": 0,
        "replan_categories": [],
        "selected_categories": None,
    }
