"""LangGraph state contract for The Bourdain Brief."""

import operator
from typing import Annotated, TypedDict

from app.models.schemas import (
    Category,
    ItineraryDay,
    ScoredRecommendation,
)


class BriefState(TypedDict):
    """Shared state passed through the travel research graph."""

    # The requested destination anchors every research, grading, and itinerary decision.
    destination: str
    city_slug: str
    # Trip length determines how many itinerary days later assembly must produce.
    trip_length_days: int
    # Categories preserve the supervisor's chosen research lanes for downstream search.
    categories: list[Category]
    # Selected categories are absent until the user confirms which research lanes to keep.
    selected_categories: list[Category] | None
    # Research iteration caps category selection revision at one additional pass.
    research_iteration: int
    # Replacement-only categories drive the bounded re-plan Send fan-out.
    replan_categories: list[Category]
    # Scored recommendation lists from Send branches append into one shared collection.
    scored_recommendations: Annotated[list[ScoredRecommendation], operator.add]
    # User selections are absent until HITL resume provides the candidate ids to keep.
    user_selections: list[str] | None
    # The itinerary is absent until final assembly converts selected recommendations into days.
    itinerary: list[ItineraryDay] | None
