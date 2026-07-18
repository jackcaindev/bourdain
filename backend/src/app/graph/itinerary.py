"""Deterministic, geo-aware itinerary assembly for selected recommendations."""

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
import logging
from math import asin, cos, radians, sin, sqrt
from typing import cast

from app.graph.state import BriefState
from app.models.schemas import (
    Category,
    ItineraryDay,
    ItinerarySlot,
    ScoredRecommendation,
    TimeBlock,
)


logger = logging.getLogger(__name__)

BLOCK_TARGET_MINUTES: dict[TimeBlock, int] = {
    "morning": 240,
    "afternoon": 240,
    "night": 180,
}

Coordinate = tuple[float, float]


@dataclass(frozen=True)
class _CategoryChoice:
    category: Category
    occupant: ScoredRecommendation
    coordinate: Coordinate | None


@dataclass
class _SlotPlan:
    activity: ScoredRecommendation | None = None
    meals: list[ScoredRecommendation] = field(default_factory=list)


@dataclass
class _DayPlan:
    slots: dict[TimeBlock, _SlotPlan]
    remaining_minutes: dict[TimeBlock, float]
    choices: list[_CategoryChoice] = field(default_factory=list)
    activity_choices: list[tuple[TimeBlock, _CategoryChoice]] = field(
        default_factory=list
    )


def _centroid(recommendations: list[ScoredRecommendation]) -> Coordinate | None:
    coordinates = [
        (recommendation.lat, recommendation.lng)
        for recommendation in recommendations
        if recommendation.lat is not None and recommendation.lng is not None
    ]
    if not coordinates:
        return None
    return (
        sum(lat for lat, _ in coordinates) / len(coordinates),
        sum(lng for _, lng in coordinates) / len(coordinates),
    )


def _distance(left: Coordinate | None, right: Coordinate | None) -> float:
    if left is None or right is None:
        return float("inf")
    left_lat, left_lng = map(radians, left)
    right_lat, right_lng = map(radians, right)
    lat_delta = right_lat - left_lat
    lng_delta = right_lng - left_lng
    haversine = (
        sin(lat_delta / 2) ** 2
        + cos(left_lat) * cos(right_lat) * sin(lng_delta / 2) ** 2
    )
    return 2 * 6371.0 * asin(sqrt(haversine))


def _trip_blocks(state: BriefState) -> list[TimeBlock]:
    blocks: list[TimeBlock] = []
    for raw_block in state["time_blocks"]:
        if raw_block in BLOCK_TARGET_MINUTES and raw_block not in blocks:
            blocks.append(cast(TimeBlock, raw_block))
    return blocks


def _category_choices(
    state: BriefState, trip_blocks: list[TimeBlock]
) -> list[_CategoryChoice]:
    selected_ids = set(state["user_selections"] or [])
    category_lookup = {
        category.name: category for category in state["selected_categories"] or []
    }
    selected_by_category: dict[str, list[ScoredRecommendation]] = {}
    for recommendation in state["scored_recommendations"]:
        if recommendation.id not in selected_ids:
            continue
        if recommendation.category not in category_lookup:
            logger.warning(
                "itinerary_recommendation_dropped",
                extra={
                    "recommendation_id": recommendation.id,
                    "category": recommendation.category,
                    "reason": "category_not_selected",
                },
            )
            continue
        selected_by_category.setdefault(recommendation.category, []).append(
            recommendation
        )

    full_pool_by_category: dict[str, list[ScoredRecommendation]] = {}
    for recommendation in state["scored_recommendations"]:
        full_pool_by_category.setdefault(recommendation.category, []).append(
            recommendation
        )

    choices: list[_CategoryChoice] = []
    for category_name, selected in selected_by_category.items():
        category = category_lookup[category_name]
        eligible_blocks = set(category.eligible_blocks).intersection(trip_blocks)
        if not eligible_blocks:
            logger.warning(
                "itinerary_category_dropped",
                extra={
                    "category": category_name,
                    "reason": "no_eligible_trip_blocks",
                },
            )
            continue
        occupant = max(
            selected,
            key=lambda recommendation: (
                recommendation.bourdain_score,
                recommendation.relevance_score,
            ),
        )
        choices.append(
            _CategoryChoice(
                category=category,
                occupant=occupant,
                coordinate=_centroid(full_pool_by_category[category_name]),
            )
        )
    return choices


def _eligible_blocks(
    choice: _CategoryChoice, trip_blocks: list[TimeBlock]
) -> list[TimeBlock]:
    return [
        block for block in trip_blocks if block in choice.category.eligible_blocks
    ]


def _activity_block_options(
    choice: _CategoryChoice, trip_blocks: list[TimeBlock]
) -> list[tuple[TimeBlock, ...]]:
    eligible = _eligible_blocks(choice, trip_blocks)
    duration = choice.category.estimated_duration_minutes or 0
    if (
        len(eligible) >= 2
        and duration > max(BLOCK_TARGET_MINUTES[block] for block in eligible)
    ):
        return list(combinations(eligible, 2))
    return [(block,) for block in eligible]


def _day_proximity(day: _DayPlan, coordinate: Coordinate | None) -> float:
    return min(
        (_distance(coordinate, placed.coordinate) for placed in day.choices),
        default=float("inf"),
    )


def _place_activities(
    choices: list[_CategoryChoice],
    days: list[_DayPlan],
    trip_blocks: list[TimeBlock],
) -> None:
    activities = sorted(
        (choice for choice in choices if choice.category.type == "activity"),
        key=lambda choice: choice.category.estimated_duration_minutes or 0,
        reverse=True,
    )
    for choice in activities:
        block_options = _activity_block_options(choice, trip_blocks)
        scope = choice.category.neighborhood_scope
        scope_days = {
            day_index
            for day_index, day in enumerate(days)
            if any(
                placed.category.neighborhood_scope == scope
                for placed in day.choices
            )
        }
        placements: list[
            tuple[tuple[float | int, ...], int, tuple[TimeBlock, ...]]
        ] = []
        duration = choice.category.estimated_duration_minutes or 0
        for day_index, day in enumerate(days):
            for blocks in block_options:
                if any(day.slots[block].activity is not None for block in blocks):
                    continue
                share = duration / len(blocks)
                overflow = sum(
                    max(0.0, share - day.remaining_minutes[block])
                    for block in blocks
                )
                cross_day_penalty = int(
                    bool(scope_days) and day_index not in scope_days
                )
                score: tuple[float | int, ...] = (
                    cross_day_penalty,
                    int(overflow > 0),
                    _day_proximity(day, choice.coordinate),
                    overflow,
                    day_index,
                    *(trip_blocks.index(block) for block in blocks),
                )
                placements.append((score, day_index, blocks))

        if not placements:
            logger.warning(
                "itinerary_category_dropped",
                extra={
                    "category": choice.category.name,
                    "reason": "no_open_activity_slot",
                },
            )
            continue

        _, day_index, blocks = min(placements, key=lambda placement: placement[0])
        day = days[day_index]
        share = duration / len(blocks)
        for block in blocks:
            day.slots[block].activity = choice.occupant
            day.remaining_minutes[block] -= share
            day.activity_choices.append((block, choice))
        day.choices.append(choice)


def _meal_placement_score(
    choice: _CategoryChoice,
    day: _DayPlan,
    block: TimeBlock,
    destination_coordinate: Coordinate,
    *,
    day_index: int,
    block_index: int,
) -> tuple[float | int, ...]:
    same_block_activities = [
        activity_choice
        for activity_block, activity_choice in day.activity_choices
        if activity_block == block
    ]
    if same_block_activities:
        rank = 0
        distance = min(
            _distance(choice.coordinate, activity.coordinate)
            for activity in same_block_activities
        )
    elif day.activity_choices:
        rank = 1
        distance = min(
            _distance(choice.coordinate, activity.coordinate)
            for _, activity in day.activity_choices
        )
    else:
        rank = 2
        distance = _distance(choice.coordinate, destination_coordinate)
    return (
        rank,
        distance,
        len(day.slots[block].meals),
        day_index,
        block_index,
    )


def _place_food(
    choices: list[_CategoryChoice],
    days: list[_DayPlan],
    trip_blocks: list[TimeBlock],
    destination_coordinate: Coordinate,
) -> None:
    for choice in (
        choice for choice in choices if choice.category.type == "food"
    ):
        placements = [
            (
                _meal_placement_score(
                    choice,
                    day,
                    block,
                    destination_coordinate,
                    day_index=day_index,
                    block_index=trip_blocks.index(block),
                ),
                day_index,
                block,
            )
            for day_index, day in enumerate(days)
            for block in _eligible_blocks(choice, trip_blocks)
        ]
        if not placements:
            continue
        _, day_index, block = min(placements, key=lambda placement: placement[0])
        days[day_index].slots[block].meals.append(choice.occupant)
        days[day_index].choices.append(choice)


def _neighborhood_focus(day: _DayPlan) -> str | None:
    scopes = [
        choice.category.neighborhood_scope
        for choice in day.choices
        if choice.category.neighborhood_scope
    ]
    if not scopes:
        return None
    counts = Counter(scopes)
    highest_count = max(counts.values())
    leaders = [scope for scope, count in counts.items() if count == highest_count]
    return leaders[0] if len(leaders) == 1 else None


def assemble_itinerary(state: BriefState) -> dict[str, list[ItineraryDay]]:
    """Place one selected occupant per category into geo-aware daily slots."""

    trip_blocks = _trip_blocks(state)
    choices = _category_choices(state, trip_blocks)
    days = [
        _DayPlan(
            slots={block: _SlotPlan() for block in trip_blocks},
            remaining_minutes={
                block: float(BLOCK_TARGET_MINUTES[block]) for block in trip_blocks
            },
        )
        for _ in range(state["trip_length_days"])
    ]
    if not days:
        return {"itinerary": []}

    _place_activities(choices, days, trip_blocks)
    _place_food(
        choices,
        days,
        trip_blocks,
        (state["destination_lat"], state["destination_lng"]),
    )

    itinerary = [
        ItineraryDay(
            day_number=day_index + 1,
            slots=[
                ItinerarySlot(
                    time_block=block,
                    activity=day.slots[block].activity,
                    meals=day.slots[block].meals,
                )
                for block in trip_blocks
                if day.slots[block].activity is not None
                or day.slots[block].meals
            ],
            neighborhood_focus=_neighborhood_focus(day),
        )
        for day_index, day in enumerate(days)
    ]
    return {"itinerary": itinerary}
