"""Bounded supervisor check for revising poor-fit research categories."""

import json
import logging
from typing import Any

from pydantic import BaseModel, TypeAdapter

from app.graph.state import BriefState
from app.models.schemas import Category, ScoredRecommendation
from app.services.llm import call_forced_tool


logger = logging.getLogger(__name__)

SUPERVISOR_REPLAN_MODEL = "claude-sonnet-4-6"


class _CategoryReplacement(BaseModel):
    category_name: str
    replacement: Category


_REPLACEMENTS_ADAPTER = TypeAdapter(list[_CategoryReplacement])


def _replan_tool_schema() -> dict[str, Any]:
    return {
        "name": "revise_research_categories",
        "description": (
            "Replaces research categories whose selection was a poor fit for the "
            "destination after research and fallback completed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "replacements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category_name": {
                                "type": "string",
                                "description": (
                                    "Exact name of an existing category to replace."
                                ),
                            },
                            "replacement": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "rationale": {"type": "string"},
                                },
                                "required": ["name", "rationale"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["category_name", "replacement"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["replacements"],
            "additionalProperties": False,
        },
    }


def _grouped_candidate_payload(state: BriefState) -> list[dict[str, Any]]:
    grouped: dict[str, list[ScoredRecommendation]] = {
        category.name: [] for category in state["categories"]
    }
    for recommendation in state["scored_recommendations"]:
        if recommendation.category in grouped:
            grouped[recommendation.category].append(recommendation)

    return [
        {
            "category": category.model_dump(mode="json"),
            "scored_recommendations": [
                recommendation.model_dump(mode="json")
                for recommendation in grouped[category.name]
            ],
        }
        for category in state["categories"]
    ]


def _validate_replacements(
    replacements: list[_CategoryReplacement], categories: list[Category]
) -> None:
    existing_names = {category.name for category in categories}
    target_names = [replacement.category_name for replacement in replacements]
    unknown_names = set(target_names) - existing_names
    if unknown_names:
        raise ValueError(
            "re-plan returned unknown category name(s): "
            f"{', '.join(sorted(unknown_names))}"
        )
    if len(target_names) != len(set(target_names)):
        raise ValueError("re-plan returned duplicate category replacement targets")

    retained_names = existing_names - set(target_names)
    replacement_names = [item.replacement.name for item in replacements]
    if len(replacement_names) != len(set(replacement_names)):
        raise ValueError("re-plan returned duplicate replacement category names")
    conflicting_names = retained_names.intersection(replacement_names)
    if conflicting_names:
        raise ValueError(
            "re-plan replacement conflicts with retained category name(s): "
            f"{', '.join(sorted(conflicting_names))}"
        )


def _request_replacements(state: BriefState) -> list[_CategoryReplacement]:
    tool_input = call_forced_tool(
        system_prompt=(
            "You are the category-quality supervisor for The Bourdain Brief. "
            "Judge whether each research category itself was a poor fit for this "
            "destination after CRAG grading and any web fallback already completed. "
            "Do not replace a category merely because its evidence is weak; CRAG owns "
            "evidence recovery. Replace it only when persistent thin or low-confidence "
            "results indicate that the selected research lane was wrong for the "
            "destination. Return no replacements when the selection was reasonable. "
            "Use the provided tool exactly once."
        ),
        user_prompt=(
            f"Destination: {state['destination']}\n"
            f"Trip length: {state['trip_length_days']} day(s)\n\n"
            "Review these categories and their final scored recommendations. For every "
            "category you replace, echo its exact name and provide one destination-"
            "specific replacement category:\n"
            f"{json.dumps(_grouped_candidate_payload(state), ensure_ascii=False)}"
        ),
        tool_schema=_replan_tool_schema(),
        model=SUPERVISOR_REPLAN_MODEL,
        max_tokens=1600,
    )
    replacements = _REPLACEMENTS_ADAPTER.validate_python(
        tool_input["replacements"]
    )
    _validate_replacements(replacements, state["categories"])
    return replacements


def supervisor_replan_check(state: BriefState) -> dict[str, Any]:
    """Revise poor-fit categories at most once without blocking the main graph."""

    if state["research_iteration"] >= 1:
        return {"replan_categories": []}

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            replacements = _request_replacements(state)
            break
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                logger.warning(
                    "supervisor_replan_retry_attempt",
                    extra={
                        "destination": state["destination"],
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
    else:
        logger.warning(
            "supervisor_replan_check_incomplete",
            extra={
                "destination": state["destination"],
                "error": str(last_error) if last_error else None,
                "error_type": type(last_error).__name__ if last_error else None,
            },
        )
        return {"replan_categories": [], "research_iteration": 1}

    if not replacements:
        return {"replan_categories": [], "research_iteration": 1}

    replaced_names = {replacement.category_name for replacement in replacements}
    retained = [
        category
        for category in state["categories"]
        if category.name not in replaced_names
    ]
    new_categories = [replacement.replacement for replacement in replacements]
    return {
        "categories": retained + new_categories,
        "replan_categories": new_categories,
        "research_iteration": 1,
    }
