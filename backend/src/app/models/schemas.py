"""Pydantic data contracts for The Bourdain Brief travel research pipeline."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


ActivityDriver = Literal[
    "Nightlife",
    "Arts & Music",
    "Culture & History",
    "Outdoors & Nature",
    "Shopping & Markets",
    "Local Life & Offbeat",
]
FoodSelection = Literal["Breakfast", "Coffee", "Lunch", "Tea", "Dinner"]
TimeBlock = Literal["morning", "afternoon", "night"]


class Category(BaseModel):
    """A persisted research lane generated from one checked trip driver."""

    id: UUID | None = None

    name: str = Field(
        description=(
            "Names the research lane so downstream candidate search can target "
            "a specific kind of place or experience."
        )
    )
    rationale: str = Field(
        default="",
        description=(
            "Captures the supervisor's reasoning so the category choice can be "
            "audited and explained for this specific destination."
        )
    )
    type: Literal["food", "activity"] | None = None
    source_drivers: list[str] = Field(default_factory=list)
    estimated_duration_minutes: int | None = Field(default=None, gt=0)
    eligible_blocks: list[TimeBlock] = Field(default_factory=list)
    status: Literal["candidate", "selected", "stale_replaced"] = "candidate"
    neighborhood_scope: str = Field(
        default="",
        description=(
            "Narrows venue research to a destination neighborhood when one has "
            "already been selected; older category producers may leave it empty."
        ),
    )


class Candidate(BaseModel):
    """A raw place or experience found before grading and scoring."""

    id: str = Field(
        description=(
            "Provides the stable match key for user_selections so downstream "
            "code can identify the exact candidate; this is not display text."
        )
    )
    name: str = Field(
        description=(
            "Provides the display name shown to users while allowing separate "
            "candidates to share the same visible name."
        )
    )
    category: str = Field(
        description=(
            "Preserves which supervisor category produced the candidate so "
            "coverage can be balanced across research lanes."
        )
    )
    description: str = Field(
        description=(
            "Summarizes the candidate in user-readable language before any "
            "grader-specific interpretation is added."
        )
    )
    lat: float | None = None
    lng: float | None = None
    internal_place_id: UUID | None = None
    place_id: str | None = None
    formatted_address: str | None = None
    google_types: list[str] = Field(default_factory=list)
    source: Literal["vector_store", "web_search"] = Field(
        description=(
            "Records the discovery channel so the app can distinguish local "
            "retrieval results from fallback web search results."
        )
    )
    source_url: str | None = Field(
        default=None,
        description=(
            "Carries an optional citation target when the discovery source can "
            "provide one without requiring every internal result to have a URL."
        ),
    )
    raw_signal: str = Field(
        description=(
            "Stores the snippet or context passed to the grader so grading "
            "decisions remain traceable to the original evidence."
        )
    )


class GradedCandidate(Candidate):
    """A candidate with grader judgment attached while retaining discovery history."""

    relevance_score: float = Field(
        description=(
            "Provides an informational fit signal for ranking discussions "
            "without controlling fallback behavior by threshold."
        )
    )
    authenticity_signal: str = Field(
        description=(
            "Captures the grader's reasoning about why the candidate does or "
            "does not match the brief's local, specific travel intent."
        )
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Communicates how strongly the grader trusts its assessment so "
            "later stages can present uncertainty honestly."
        )
    )
    needs_fallback: bool = Field(
        description=(
            "Stores the grader's explicit judgment that more search is needed, "
            "rather than deriving fallback from a numeric score."
        )
    )


class ScoredRecommendation(GradedCandidate):
    """A graded candidate with final recommendation scoring and guardrail context."""

    bourdain_score: int = Field(
        description=(
            "Represents the final 1-5 editorial fit score used to prioritize "
            "recommendations for the user."
        )
    )
    scoring_rationale: str = Field(
        description=(
            "Explains why the recommendation earned its score so the UI can "
            "show reasoning instead of an unexplained rating."
        )
    )
    locally_owned_signal: str | None = Field(
        default=None,
        description=(
            "Carries any opportunistic local-ownership clue while avoiding a "
            "verified factual claim when that signal is absent or uncertain."
        ),
    )
    passed_guardrail: bool = Field(
        description=(
            "Indicates whether the recommendation cleared safety and quality "
            "guardrails while still preserving flagged items for review."
        )
    )
    guardrail_note: str | None = Field(
        default=None,
        description=(
            "Explains a guardrail flag when present so the user can understand "
            "why a recommendation needs caution."
        ),
    )


class ItinerarySlot(BaseModel):
    time_block: TimeBlock
    activity: ScoredRecommendation | None = None
    meals: list[ScoredRecommendation] = Field(default_factory=list)


class ItineraryDay(BaseModel):
    day_number: int
    slots: list[ItinerarySlot]
    neighborhood_focus: str | None = None


class CandidatePayload(BaseModel):
    """Progress payload for candidate discovery updates."""

    category: str = Field(
        description=(
            "Names the category being searched so progress updates can be "
            "attached to the correct research lane."
        )
    )
    candidates_found: int = Field(
        description=(
            "Reports discovery volume so the UI can show concrete progress "
            "without exposing raw candidate objects."
        )
    )


class ScorePayload(BaseModel):
    """Progress payload for recommendation scoring updates."""

    recommendation: ScoredRecommendation = Field(
        description=(
            "Carries the scored recommendation that just became available so "
            "the UI can stream meaningful results."
        )
    )


class FallbackPayload(BaseModel):
    """Progress payload for fallback search decisions."""

    category: str = Field(
        description=(
            "Identifies which research lane needs fallback so the UI can show "
            "where additional search is happening."
        )
    )
    reason: str = Field(
        description=(
            "Explains why fallback was triggered so the app can distinguish "
            "low confidence from missing or weak evidence."
        )
    )


class ErrorPayload(BaseModel):
    """Progress payload for node-level errors."""

    node_name: str = Field(
        description=(
            "Identifies the failing node so logs and user-visible progress can "
            "point to the stage that needs attention."
        )
    )
    detail: str = Field(
        description=(
            "Provides the error detail needed for debugging while keeping the "
            "outer event envelope consistent."
        )
    )


class CategoryListPayload(BaseModel):
    categories: list[Category]


class HitlPayload(BaseModel):
    """Recommendations presented when the graph pauses for user selection."""

    recommendations: list[ScoredRecommendation] = Field(
        description=(
            "Carries the complete reviewed recommendation set so the client can "
            "render the human-in-the-loop selection screen."
        )
    )


class ItineraryPayload(BaseModel):
    """Completed itinerary delivered when assembly finishes."""

    days: list[ItineraryDay] = Field(
        description=(
            "Carries the assembled days so the client can render the final "
            "itinerary without making a separate request."
        )
    )


class SSEEvent(BaseModel):
    """A typed server-sent event envelope for UI progress streaming."""

    event_type: Literal[
        "node_start",
        "node_progress",
        "node_complete",
        "hitl_pause",
        "error",
    ] = Field(
        description=(
            "Classifies the progress event so the UI can render starts, updates, "
            "pauses, completions, and errors consistently."
        )
    )
    node_name: str = Field(
        description=(
            "Names the graph node associated with the event so progress can be "
            "grouped by pipeline stage."
        )
    )
    message: str = Field(
        description=(
            "Provides human-readable progress text intended to be rendered "
            "directly in the UI feed."
        )
    )
    payload: (
        CandidatePayload
        | ScorePayload
        | FallbackPayload
        | ErrorPayload
        | CategoryListPayload
        | HitlPayload
        | ItineraryPayload
        | None
    ) = Field(
        default=None,
        description=(
            "Carries one of the typed event payloads when structured progress "
            "data is available, avoiding untyped dictionaries."
        ),
    )
