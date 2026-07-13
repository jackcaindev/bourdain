"""Deterministic itinerary assembly for selected recommendations."""

from collections import Counter

from app.graph.state import BriefState
from app.models.schemas import ItineraryDay, ScoredRecommendation


# Known v1 simplification: category keywords are not a solved classification
# system, and ambiguous categories such as "night market" could reasonably fit
# either meals or activities.
MEAL_CATEGORY_KEYWORDS = (
    "food",
    "restaurant",
    "market",
    "bar",
    "cafe",
    "café",
    "dining",
    "eat",
)
MEAL_SLOTS = ("breakfast", "lunch", "dinner")
MAX_ACTIVITIES_PER_DAY = 2


def _is_meal_candidate(recommendation: ScoredRecommendation) -> bool:
    category = recommendation.category.casefold()
    return any(keyword in category for keyword in MEAL_CATEGORY_KEYWORDS)


def _neighborhood_focus(
    recommendations: list[ScoredRecommendation],
) -> str | None:
    """Return the unique most-common category, or None for an empty day or tie."""

    if not recommendations:
        return None

    category_counts = Counter(item.category for item in recommendations)
    highest_count = max(category_counts.values())
    leaders = [
        category
        for category, count in category_counts.items()
        if count == highest_count
    ]
    return leaders[0] if len(leaders) == 1 else None


def assemble_itinerary(state: BriefState) -> dict[str, list[ItineraryDay]]:
    """Distribute selected recommendations into fixed daily itinerary slots."""

    day_count = state["trip_length_days"]
    selected_ids = set(state["user_selections"] or [])
    selected = [
        recommendation
        for recommendation in state["scored_recommendations"]
        if recommendation.id in selected_ids
    ]

    days: list[dict[str, object]] = [
        {
            "day_number": day_index + 1,
            "breakfast": None,
            "lunch": None,
            "dinner": None,
            "activities": [],
        }
        for day_index in range(day_count)
    ]

    if not days:
        return {"itinerary": []}

    meals = [item for item in selected if _is_meal_candidate(item)]
    activities = [item for item in selected if not _is_meal_candidate(item)]

    for index, recommendation in enumerate(meals[: day_count * len(MEAL_SLOTS)]):
        day_index = index % day_count
        slot = MEAL_SLOTS[index // day_count]
        days[day_index][slot] = recommendation

    activity_capacity = day_count * MAX_ACTIVITIES_PER_DAY
    for index, recommendation in enumerate(activities[:activity_capacity]):
        day_index = index % day_count
        day_activities = days[day_index]["activities"]
        assert isinstance(day_activities, list)
        day_activities.append(recommendation)

    itinerary: list[ItineraryDay] = []
    for day in days:
        assigned = [
            item
            for slot in MEAL_SLOTS
            if isinstance((item := day[slot]), ScoredRecommendation)
        ]
        day_activities = day["activities"]
        assert isinstance(day_activities, list)
        assigned.extend(day_activities)

        itinerary.append(
            ItineraryDay(
                **day,
                # This is a soft category label, not location-based clustering;
                # the pipeline has no geographic data at this stage.
                neighborhood_focus=_neighborhood_focus(assigned),
            )
        )

    return {"itinerary": itinerary}
