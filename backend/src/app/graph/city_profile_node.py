"""Load or derive destination-specific city profile categories."""

import asyncio
import logging
from typing import Annotated, Any

from pydantic import Field, TypeAdapter

from app.graph.state import BriefState
from app.models.schemas import Category
from app.services.city_profiles import (
    get_city_profile,
    normalize_city_slug,
    save_city_profile,
)
from app.services.llm import call_forced_tool


logger = logging.getLogger(__name__)

CITY_PROFILE_MODEL = "claude-haiku-4-5"
_CATEGORY_LIST_ADAPTER = TypeAdapter(
    Annotated[list[Category], Field(min_length=5, max_length=8)]
)


def _target_category_count(trip_length_days: int) -> int:
    """Scale category breadth loosely with trip length, bounded to 5-8."""

    return min(8, max(5, 4 + (trip_length_days + 1) // 2))


def _category_tool_schema() -> dict[str, Any]:
    return {
        "name": "derive_city_profile_categories",
        "description": (
            "Derives authentic, destination-specific research categories for a "
            "city profile."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "minItems": 5,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "A concise, destination-specific research lane."
                                ),
                            },
                            "rationale": {
                                "type": "string",
                                "description": (
                                    "Why this category reveals authentic local "
                                    "character in this destination."
                                ),
                            },
                        },
                        "required": ["name", "rationale"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["categories"],
            "additionalProperties": False,
        },
    }


def _system_prompt() -> str:
    return (
        "You build persistent city profiles for The Bourdain Brief. Derive "
        "research categories that reveal how this specific place actually lives, "
        "with authentic, non-touristy local character rather than a generic tourist "
        "checklist. Favor locally grounded lanes such as street food traditions, "
        "independent live music venues, neighborhood markets, local craft, and "
        "outdoor activities distinctive to the place, but only when they genuinely "
        "fit the destination. Avoid broad categories like sightseeing, attractions, "
        "shopping, or restaurants when a sharper local lens is possible. Use the "
        "provided tool exactly once."
    )


def _user_prompt(
    destination: str,
    trip_length_days: int,
    target_category_count: int,
) -> str:
    return (
        f"Destination: {destination}\n"
        f"Trip length: {trip_length_days} day(s)\n\n"
        f"Derive {target_category_count} distinct research categories for this "
        "city. Make every category specific enough to steer research toward "
        "authentic, non-touristy places or experiences. Each rationale must "
        "explain what the category reveals about this destination and why it fits "
        "the available trip length."
    )


def _derive_categories(
    destination: str,
    trip_length_days: int,
) -> list[Category]:
    target_category_count = _target_category_count(trip_length_days)
    tool_input = call_forced_tool(
        system_prompt=_system_prompt(),
        user_prompt=_user_prompt(
            destination,
            trip_length_days,
            target_category_count,
        ),
        tool_schema=_category_tool_schema(),
        model=CITY_PROFILE_MODEL,
        max_tokens=1400,
    )
    return _CATEGORY_LIST_ADAPTER.validate_python(tool_input["categories"])


async def city_profile_node(
    state: BriefState,
) -> dict[str, str | int | list[Category] | None]:
    """Return stored city categories, deriving and persisting them on a miss."""

    destination = state["destination"]
    trip_length_days = state["trip_length_days"]
    city_slug = normalize_city_slug(destination)

    logger.info(
        "city_profile_node_start",
        extra={
            "destination": destination,
            "city_slug": city_slug,
            "trip_length_days": trip_length_days,
        },
    )

    try:
        categories = await get_city_profile(city_slug)
    except Exception:
        logger.exception(
            "city_profile_node_lookup_failed",
            extra={"destination": destination, "city_slug": city_slug},
        )
        raise

    if categories is not None:
        logger.info(
            "city_profile_node_complete",
            extra={
                "destination": destination,
                "city_slug": city_slug,
                "category_count": len(categories),
                "profile_source": "database",
            },
        )
        return {
            "city_slug": city_slug,
            "categories": categories,
            "research_iteration": 0,
            "replan_categories": [],
            "selected_categories": None,
        }

    try:
        categories = await asyncio.to_thread(
            _derive_categories,
            destination,
            trip_length_days,
        )
    except Exception:
        logger.exception(
            "city_profile_node_generation_failed",
            extra={"destination": destination, "city_slug": city_slug},
        )
        raise

    try:
        await save_city_profile(city_slug, destination, categories)
    except Exception:
        logger.exception(
            "city_profile_node_save_failed",
            extra={
                "destination": destination,
                "city_slug": city_slug,
                "category_count": len(categories),
            },
        )
        raise

    logger.info(
        "city_profile_node_complete",
        extra={
            "destination": destination,
            "city_slug": city_slug,
            "category_count": len(categories),
            "profile_source": "llm",
        },
    )
    return {
        "city_slug": city_slug,
        "categories": categories,
        "research_iteration": 0,
        "replan_categories": [],
        "selected_categories": None,
    }
