"""Manual real-API quality evaluation for the compiled Bourdain Brief graph.

Run from ``backend/`` with ``uv run python scripts/run_eval.py``. This is
intentionally not a pytest test: it calls paid external APIs and a real database.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Iterable
from uuid import UUID, uuid4

from langgraph.types import Command

from app.db.itineraries import get_itinerary_with_details
from app.db.trips import create_trip
from app.graph.build import build_graph
from app.graph.itinerary import BLOCK_TARGET_MINUTES
from app.logging_config import configure_logging
from app.models.schemas import (
    Category,
    CategoryListPayload,
    HitlPayload,
    PersistedItineraryResponse,
    ScoredRecommendation,
    TimeBlock,
)
from app.services.places import close_shared_client, resolve_city
from app.services.vector_store import get_shared_pool


REQUIRED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "GOOGLE_PLACES_API_KEY",
    "DATABASE_URL",
)
MAX_CATEGORIES_PER_DRIVER = 2


@dataclass(frozen=True)
class EvalCase:
    destination: str
    trip_length_days: int
    activity_drivers: tuple[str, ...]
    food_selections: tuple[str, ...]
    time_blocks: tuple[TimeBlock, ...]


CORPUS = (
    EvalCase(
        "Paris, France",
        3,
        ("Culture & History", "Arts & Music"),
        ("Coffee", "Dinner"),
        ("morning", "afternoon", "night"),
    ),
    EvalCase(
        "Tokyo, Japan",
        4,
        ("Local Life & Offbeat", "Shopping & Markets"),
        ("Lunch", "Dinner"),
        ("afternoon", "night"),
    ),
    EvalCase(
        "Mexico City, Mexico",
        3,
        ("Culture & History", "Nightlife"),
        ("Breakfast", "Lunch", "Dinner"),
        ("morning", "afternoon", "night"),
    ),
    EvalCase(
        "Ghent, Belgium",
        2,
        ("Arts & Music", "Local Life & Offbeat"),
        ("Coffee", "Dinner"),
        ("afternoon", "night"),
    ),
    EvalCase(
        "Ljubljana, Slovenia",
        3,
        ("Outdoors & Nature", "Culture & History"),
        ("Breakfast", "Lunch"),
        ("morning", "afternoon"),
    ),
    EvalCase(
        "Takamatsu, Kagawa, Japan",
        2,
        ("Local Life & Offbeat", "Arts & Music"),
        ("Breakfast", "Dinner"),
        ("morning", "night"),
    ),
)


class ResearchMetricHandler(logging.Handler):
    """Collect the existing retrieval and Places-verification log events."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"retrieved": 0, "verified": 0}
        )

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() not in {
            "research_retrieval_complete",
            "research_verification_complete",
        }:
            return
        category = getattr(record, "category", None)
        candidate_count = getattr(record, "candidate_count", None)
        if not isinstance(category, str) or not isinstance(candidate_count, int):
            return
        key = (
            "retrieved"
            if record.getMessage() == "research_retrieval_complete"
            else "verified"
        )
        self.counts[category][key] += candidate_count

    def summary(self) -> dict[str, dict[str, int | float | None]]:
        result: dict[str, dict[str, int | float | None]] = {}
        for category, counts in sorted(self.counts.items()):
            retrieved = counts["retrieved"]
            verified = counts["verified"]
            result[category] = {
                "retrieved_count": retrieved,
                "verified_count": verified,
                "resolution_rate": verified / retrieved if retrieved else None,
            }
        return result


def _require_environment() -> None:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        names = ", ".join(missing)
        raise SystemExit(
            "Cannot run the real-API evaluation. Missing required environment "
            f"variable(s): {names}"
        )


def _interrupt_payload(result: dict[str, Any], expected_type: type[Any]) -> Any:
    interrupts = result.get("__interrupt__") or []
    if not interrupts:
        raise RuntimeError(f"Graph did not pause for {expected_type.__name__}")
    payload = interrupts[0].value
    if not isinstance(payload, expected_type):
        raise RuntimeError(
            f"Expected {expected_type.__name__} interrupt, got "
            f"{type(payload).__name__}"
        )
    return payload


def _select_categories(case: EvalCase, offered: list[Category]) -> list[str]:
    """Select a balanced, budget-bounded subset of placeable research lanes."""

    trip_blocks = set(case.time_blocks)
    candidates = [
        category
        for category in offered
        if trip_blocks.intersection(category.eligible_blocks)
    ]
    budget = sum(
        BLOCK_TARGET_MINUTES[block] * case.trip_length_days
        for block in case.time_blocks
    )
    remaining_by_block = {
        block: BLOCK_TARGET_MINUTES[block] * case.trip_length_days
        for block in case.time_blocks
    }
    selected: list[Category] = []
    driver_counts: dict[str, int] = defaultdict(int)
    used_minutes = 0

    def source_driver(category: Category) -> str:
        return category.source_drivers[0] if category.source_drivers else category.name

    while candidates:
        ranked: list[tuple[tuple[float, ...], Category, TimeBlock]] = []
        for category in candidates:
            driver = source_driver(category)
            duration = category.estimated_duration_minutes or 0
            if driver_counts[driver] >= MAX_CATEGORIES_PER_DRIVER:
                continue
            if used_minutes + duration > budget:
                continue
            eligible = [
                block for block in case.time_blocks if block in category.eligible_blocks
            ]
            if not eligible:
                continue
            target_block = max(
                eligible,
                key=lambda block: remaining_by_block[block]
                / BLOCK_TARGET_MINUTES[block],
            )
            is_new_driver = float(driver_counts[driver] == 0)
            block_need = max(remaining_by_block[target_block], 0) / max(
                BLOCK_TARGET_MINUTES[target_block], 1
            )
            ranked.append(
                ((is_new_driver, block_need, float(duration)), category, target_block)
            )
        if not ranked:
            break
        _, category, target_block = max(ranked, key=lambda item: item[0])
        candidates.remove(category)
        selected.append(category)
        driver_counts[source_driver(category)] += 1
        duration = category.estimated_duration_minutes or 0
        used_minutes += duration
        remaining_by_block[target_block] -= duration

    uncovered = [
        block
        for block in case.time_blocks
        if not any(block in category.eligible_blocks for category in selected)
    ]
    if uncovered:
        raise RuntimeError(
            "Offered categories could not cover checked time block(s): "
            + ", ".join(uncovered)
        )
    return [category.name for category in selected]


def _select_recommendations(
    recommendations: list[ScoredRecommendation],
) -> list[str]:
    by_category: dict[str, list[ScoredRecommendation]] = defaultdict(list)
    for recommendation in recommendations:
        by_category[recommendation.category].append(recommendation)

    selected: list[str] = []
    for category_recommendations in by_category.values():
        passed = [item for item in category_recommendations if item.passed_guardrail]
        pool = passed or category_recommendations
        winner = max(
            pool,
            key=lambda item: (
                item.bourdain_score,
                item.relevance_score,
                item.confidence == "high",
                item.confidence == "medium",
            ),
        )
        selected.append(winner.id)
    return selected


async def _delete_trip(trip_id: UUID) -> None:
    pool = await get_shared_pool()
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM trips WHERE id = $1", trip_id)
        remaining = await connection.fetchval(
            "SELECT count(*) FROM trips WHERE id = $1", trip_id
        )
    if remaining:
        raise RuntimeError(f"Eval trip {trip_id} still exists after cleanup")


async def _delete_checkpoints(graph: Any, session_id: str) -> None:
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is None or not hasattr(checkpointer, "adelete_thread"):
        raise RuntimeError("Compiled graph does not expose async thread cleanup")
    await checkpointer.adelete_thread(session_id)


async def _persisted_category_placements(trip_id: UUID) -> list[dict[str, Any]]:
    pool = await get_shared_pool()
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT DISTINCT
                d.day_number,
                s.time_block,
                s.slot_role,
                c.id AS category_id,
                c.name AS category_name,
                c.type AS category_type,
                c.source_drivers,
                c.eligible_blocks,
                c.estimated_duration_minutes,
                c.neighborhood_scope
            FROM itineraries i
            JOIN itinerary_days d ON d.itinerary_id = i.id
            JOIN itinerary_slots s ON s.itinerary_day_id = d.id
            JOIN recommendations r ON r.id = s.recommendation_id
            JOIN categories c ON c.id = r.category_id
            WHERE i.trip_id = $1
            ORDER BY d.day_number, s.time_block, c.name
            """,
            trip_id,
        )
    return [dict(row) for row in rows]


def _activity_options(
    category: dict[str, Any], case: EvalCase
) -> list[tuple[TimeBlock, ...]]:
    eligible = [
        block for block in case.time_blocks if block in category["eligible_blocks"]
    ]
    if not eligible:
        return []
    duration = category["estimated_duration_minutes"]
    if len(eligible) >= 2 and duration > max(
        BLOCK_TARGET_MINUTES[block] for block in eligible
    ):
        return list(combinations(eligible, 2))
    return [(block,) for block in eligible]


def _same_day_activity_assignment_possible(
    categories: list[dict[str, Any]],
    target_scope: str,
    case: EvalCase,
) -> bool:
    """Solve the small activity-slot assignment with one scope forced together."""

    activities = [item for item in categories if item["category_type"] == "activity"]
    if not activities:
        return True
    days = range(1, case.trip_length_days + 1)
    for target_day in days:
        choices: list[tuple[dict[str, Any], list[tuple[int, tuple[TimeBlock, ...]]]]] = []
        for category in activities:
            allowed_days: Iterable[int] = (
                (target_day,)
                if category["neighborhood_scope"] == target_scope
                else days
            )
            placements = [
                (day, blocks)
                for day in allowed_days
                for blocks in _activity_options(category, case)
            ]
            if not placements:
                break
            choices.append((category, placements))
        else:
            choices.sort(key=lambda item: len(item[1]))
            occupied: set[tuple[int, TimeBlock]] = set()

            def assign(index: int) -> bool:
                if index == len(choices):
                    return True
                for day, blocks in choices[index][1]:
                    slots = {(day, block) for block in blocks}
                    if occupied.isdisjoint(slots):
                        occupied.update(slots)
                        if assign(index + 1):
                            return True
                        occupied.difference_update(slots)
                return False

            if assign(0):
                return True
    return False


def _returned_signature(days: list[Any]) -> set[tuple[int, str, str, str]]:
    signature: set[tuple[int, str, str, str]] = set()
    for day in days:
        for slot in day.slots:
            if slot.activity is not None:
                signature.add(
                    (day.day_number, slot.time_block, "activity", slot.activity.category)
                )
            signature.update(
                (day.day_number, slot.time_block, "meal", meal.category)
                for meal in slot.meals
            )
    return signature


def _structural_checks(
    case: EvalCase,
    returned_days: list[Any],
    persisted: PersistedItineraryResponse,
    placements: list[dict[str, Any]],
) -> dict[str, Any]:
    persisted_signature = {
        (
            row["day_number"],
            row["time_block"],
            row["slot_role"],
            row["category_name"],
        )
        for row in placements
    }
    block_coverage = {
        block: any(row["time_block"] == block for row in placements)
        for block in case.time_blocks
    }
    meal_coverage = {
        meal: any(
            row["slot_role"] == "meal" and meal in row["source_drivers"]
            for row in placements
        )
        for meal in case.food_selections
    }

    by_category = {
        row["category_id"]: row
        for row in placements
    }
    days_by_scope_category: dict[str, dict[UUID, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in placements:
        scope = row["neighborhood_scope"]
        if scope:
            days_by_scope_category[scope][row["category_id"]].add(row["day_number"])

    avoidable_splits: list[dict[str, Any]] = []
    categories = list(by_category.values())
    for scope, category_days in days_by_scope_category.items():
        if len(category_days) < 2:
            continue
        combined_days = set().union(*category_days.values())
        if len(combined_days) <= 1:
            continue
        if _same_day_activity_assignment_possible(categories, scope, case):
            avoidable_splits.append(
                {
                    "neighborhood_scope": scope,
                    "category_days": {
                        by_category[category_id]["category_name"]: sorted(days)
                        for category_id, days in category_days.items()
                    },
                }
            )

    checks = {
        "itinerary_day_count": {
            "passed": len(persisted.days) == case.trip_length_days,
            "expected": case.trip_length_days,
            "actual": len(persisted.days),
        },
        "persisted_matches_returned": {
            "passed": persisted_signature == _returned_signature(returned_days),
        },
        "checked_time_block_coverage": {
            "passed": all(block_coverage.values()),
            "by_time_block": block_coverage,
        },
        "neighborhood_colocation": {
            "passed": not avoidable_splits,
            "avoidable_splits": avoidable_splits,
        },
        "checked_meal_coverage": {
            "passed": all(meal_coverage.values()),
            "by_meal_type": meal_coverage,
        },
    }
    return {
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


async def _run_case(graph: Any, case: EvalCase) -> dict[str, Any]:
    session_id = f"eval-{uuid4()}"
    config = {"configurable": {"thread_id": session_id}}
    trip_id: UUID | None = None
    metrics = ResearchMetricHandler()
    research_logger = logging.getLogger("app.graph.research")
    started_at = datetime.now(UTC)
    result: dict[str, Any] = {
        "destination": case.destination,
        "trip_length_days": case.trip_length_days,
        "activity_drivers": list(case.activity_drivers),
        "food_selections": list(case.food_selections),
        "time_blocks": list(case.time_blocks),
        "session_id": session_id,
        "status": "failed",
    }

    research_logger.addHandler(metrics)
    try:
        resolution = await resolve_city(case.destination)
        if resolution.status != "resolved" or resolution.match is None:
            candidates = [item.formatted_address for item in resolution.candidates]
            raise RuntimeError(
                f"Destination was ambiguous; candidates were: {candidates}"
            )
        match = resolution.match
        trip = await create_trip(
            destination_raw=case.destination,
            destination_place_id=match.google_place_id,
            destination_formatted=match.formatted_address,
            destination_lat=match.lat,
            destination_lng=match.lng,
            trip_length_days=case.trip_length_days,
            activity_drivers=list(case.activity_drivers),
            food_selections=list(case.food_selections),
            time_blocks=list(case.time_blocks),
            session_id=session_id,
        )
        trip_id = trip.id

        first = await graph.ainvoke(
            {
                "destination": case.destination,
                "trip_length_days": case.trip_length_days,
            },
            config,
        )
        category_payload = _interrupt_payload(first, CategoryListPayload)
        selected_categories = _select_categories(case, category_payload.categories)
        result["offered_category_count"] = len(category_payload.categories)
        result["selected_categories"] = selected_categories

        second = await graph.ainvoke(Command(resume=selected_categories), config)
        venue_payload = _interrupt_payload(second, HitlPayload)
        selected_recommendations = _select_recommendations(
            venue_payload.recommendations
        )
        if not selected_recommendations:
            raise RuntimeError("HITL-2 offered no selectable recommendations")
        result["offered_recommendation_count"] = len(venue_payload.recommendations)
        result["selected_recommendation_count"] = len(selected_recommendations)

        final = await graph.ainvoke(Command(resume=selected_recommendations), config)
        if final.get("__interrupt__"):
            raise RuntimeError("Graph paused unexpectedly after HITL-2")
        returned_days = final.get("itinerary")
        if not isinstance(returned_days, list):
            raise RuntimeError("Graph completed without a returned itinerary")
        persisted = await get_itinerary_with_details(trip.id)
        if persisted is None:
            raise RuntimeError("Graph completed without a persisted itinerary")
        placements = await _persisted_category_placements(trip.id)
        result["structural"] = _structural_checks(
            case, returned_days, persisted, placements
        )
        result["status"] = "completed"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        research_logger.removeHandler(metrics)
        result["places_resolution_by_category"] = metrics.summary()
        cleanup_errors: list[str] = []
        if trip_id is not None:
            try:
                await _delete_trip(trip_id)
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    f"trip: {type(cleanup_exc).__name__}: {cleanup_exc}"
                )
        try:
            await _delete_checkpoints(graph, session_id)
        except Exception as cleanup_exc:
            cleanup_errors.append(
                f"checkpoints: {type(cleanup_exc).__name__}: {cleanup_exc}"
            )
        result["cleanup"] = {
            "passed": not cleanup_errors,
            "trip_created": trip_id is not None,
            "errors": cleanup_errors,
        }
        if cleanup_errors:
            result["status"] = "failed"
        result["duration_seconds"] = round(
            (datetime.now(UTC) - started_at).total_seconds(), 3
        )
    return result


def _print_summary(result: dict[str, Any]) -> None:
    print(f"\n{result['destination']}: {result['status'].upper()}")
    metrics = result["places_resolution_by_category"]
    if metrics:
        for category, values in metrics.items():
            rate = values["resolution_rate"]
            display_rate = "n/a" if rate is None else f"{rate:.1%}"
            print(
                f"  Places {category}: {values['verified_count']}/"
                f"{values['retrieved_count']} ({display_rate})"
            )
    else:
        print("  Places: no retrieval metrics captured")
    structural = result.get("structural")
    if structural:
        print(
            "  Structural checks: "
            + ("PASS" if structural["passed"] else "FAIL")
        )
        for name, check in structural["checks"].items():
            print(f"    {name}: {'PASS' if check['passed'] else 'FAIL'}")
    if result.get("error"):
        print(f"  Error: {result['error']}")
    print(f"  Cleanup: {'PASS' if result['cleanup']['passed'] else 'FAIL'}")


async def main() -> int:
    _require_environment()
    configure_logging(level="INFO")
    started_at = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    try:
        async with build_graph() as graph:
            for case in CORPUS:
                print(f"\nRunning eval for {case.destination}...", flush=True)
                case_result = await _run_case(graph, case)
                results.append(case_result)
                _print_summary(case_result)
    finally:
        await close_shared_client()

    finished_at = datetime.now(UTC)
    payload = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "corpus_size": len(CORPUS),
        "completed_count": sum(item["status"] == "completed" for item in results),
        "structural_pass_count": sum(
            item.get("structural", {}).get("passed", False) for item in results
        ),
        "results": results,
    }
    results_dir = Path(__file__).resolve().parents[1] / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = finished_at.strftime("%Y%m%dT%H%M%SZ")
    output_path = results_dir / f"eval-{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote results to {output_path}")
    all_passed = len(results) == len(CORPUS) and all(
        item["status"] == "completed"
        and item.get("structural", {}).get("passed", False)
        and item["cleanup"]["passed"]
        for item in results
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nEvaluation interrupted.", file=sys.stderr)
        raise SystemExit(130) from None
