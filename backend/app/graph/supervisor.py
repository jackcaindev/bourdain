"""Supervisor node for selecting destination-specific research categories."""

import logging
from typing import Any

from pydantic import TypeAdapter

from app.config import ConfigurationError
from app.graph.state import BriefState
from app.models.schemas import Category
from app.services.llm import call_forced_tool


logger = logging.getLogger(__name__)

SUPERVISOR_MODEL = "claude-haiku-4-5"
_CATEGORY_LIST_ADAPTER = TypeAdapter(list[Category])


def _category_tool_schema() -> dict[str, Any]:
    return {
        "name": "select_research_categories",
        "description": (
            "Selects destination-specific research categories for a travel brief."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "Concise research category name, such as "
                                    "food, beaches, markets, music, history, "
                                    "neighborhoods, hikes, or nightlife."
                                ),
                            },
                            "rationale": {
                                "type": "string",
                                "description": (
                                    "Why this research lane fits this destination "
                                    "and trip length."
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
        "You are the supervisor for The Bourdain Brief, a travel research "
        "pipeline. Choose research categories that match the destination's "
        "actual character, not a fixed generic checklist. A beach town, dense "
        "historic city, mountain village, food capital, pilgrimage site, and "
        "remote island should produce meaningfully different research lanes. "
        "Use the provided tool exactly once."
    )


def _user_prompt(destination: str, trip_length_days: int) -> str:
    return (
        f"Destination: {destination}\n"
        f"Trip length: {trip_length_days} day(s)\n\n"
        "Select 3 to 6 research categories for discovering places and "
        "experiences that would make a specific, characterful brief. Prefer "
        "categories that reveal how this destination actually works for a "
        "traveler over broad boilerplate. Each rationale should reference the "
        "destination or trip length."
    )


def _fallback_categories() -> list[Category]:
    return [
        Category(
            name=name,
            rationale="Fallback default category used after supervisor LLM failure.",
        )
        for name in ("food", "neighborhoods", "activities")
    ]


def _select_categories(destination: str, trip_length_days: int) -> list[Category]:
    tool_schema = _category_tool_schema()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            tool_input = call_forced_tool(
                system_prompt=_system_prompt(),
                user_prompt=_user_prompt(destination, trip_length_days),
                tool_schema=tool_schema,
                model=SUPERVISOR_MODEL,
                max_tokens=800,
            )
            return _CATEGORY_LIST_ADAPTER.validate_python(tool_input["categories"])
        except ConfigurationError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                logger.warning(
                    "supervisor_retry_attempt",
                    extra={
                        "destination": destination,
                        "category_count": 0,
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )

    fallback_categories = _fallback_categories()
    logger.warning(
        "supervisor_fallback_triggered",
        extra={
            "destination": destination,
            "category_count": len(fallback_categories),
            "error": str(last_error) if last_error else None,
            "error_type": type(last_error).__name__ if last_error else None,
        },
    )
    return fallback_categories


def supervisor_node(state: BriefState) -> dict[str, list[Category]]:
    """Select research categories for the destination brief."""

    destination = state["destination"]
    trip_length_days = state["trip_length_days"]

    logger.info(
        "supervisor_node_start",
        extra={"destination": destination, "category_count": 0},
    )

    categories = _select_categories(destination, trip_length_days)

    logger.info(
        "supervisor_node_complete",
        extra={
            "destination": destination,
            "category_count": len(categories),
        },
    )

    return {"categories": categories}
