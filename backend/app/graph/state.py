"""LangGraph state contract for The Bourdain Brief."""

import operator
from typing import Annotated, TypedDict

from app.models.schemas import (
    Candidate,
    Category,
    GradedCandidate,
    ItineraryDay,
    ScoredRecommendation,
)


class BriefState(TypedDict):
    """Shared state passed through the travel research graph."""

    # The requested destination anchors every research, grading, and itinerary decision.
    destination: str
    # Trip length determines how many itinerary days later assembly must produce.
    trip_length_days: int
    # Categories preserve the supervisor's chosen research lanes for downstream search.
    categories: list[Category]
    # Candidate lists from Send API branches are appended into one shared collection.
    candidates: Annotated[list[Candidate], operator.add]
    # Graded candidates retain discovery history plus grader judgment for scoring.
    graded_candidates: list[GradedCandidate]
    # Scored recommendations hold the final ranked options before user review and assembly.
    scored_recommendations: list[ScoredRecommendation]
    # User selections are absent until HITL resume provides the candidate ids to keep.
    user_selections: list[str] | None
    # The itinerary is absent until final assembly converts selected recommendations into days.
    itinerary: list[ItineraryDay] | None
