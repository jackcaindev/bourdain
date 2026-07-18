"""Pydantic contracts for records persisted in the v2 domain schema."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DomainRecord(BaseModel):
    """Base contract for database rows returned by the persistence layer."""

    model_config = ConfigDict(frozen=True)


class TripRecord(DomainRecord):
    id: UUID
    destination_raw: str
    destination_place_id: str
    destination_formatted: str
    destination_lat: float
    destination_lng: float
    trip_length_days: int
    activity_drivers: list[str]
    food_selections: list[str]
    time_blocks: list[str]
    status: Literal[
        "gathering_categories", "researching", "reviewing", "confirmed"
    ]
    session_id: str
    created_at: datetime
    updated_at: datetime


class PlaceRecord(DomainRecord):
    id: UUID
    google_place_id: str
    name: str
    formatted_address: str
    lat: float
    lng: float
    google_types: list[str]
    resolved_at: datetime


class CategoryRecord(DomainRecord):
    id: UUID
    trip_id: UUID
    name: str
    type: Literal["food", "activity"]
    source_drivers: list[str]
    eligible_blocks: list[str]
    estimated_duration_minutes: int
    neighborhood_scope: str
    status: Literal["candidate", "selected", "stale_replaced"]
    day_number: int | None
    time_block: str | None
    created_at: datetime


class ResearchRunRecord(DomainRecord):
    id: UUID
    trip_id: UUID
    category_id: UUID
    trigger_reason: Literal[
        "initial", "crag_fallback", "supervisor_replan", "on_demand"
    ]
    iteration: int
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None


class EvidenceRecord(DomainRecord):
    id: UUID
    place_id: UUID
    research_run_id: UUID
    source_type: Literal["vector_store", "web_search", "places_api"]
    raw_content: str
    retrieved_at: datetime


class RecommendationRecord(DomainRecord):
    id: UUID
    trip_id: UUID
    category_id: UUID
    research_run_id: UUID
    place_id: UUID
    relevance_score: float
    authenticity_signal: str
    confidence: Literal["low", "medium", "high"]
    needs_fallback: bool
    bourdain_score: int
    scoring_rationale: str
    locally_owned_signal: str | None
    passed_guardrail: bool
    guardrail_note: str | None
    created_at: datetime


class CategorySelectionRecord(DomainRecord):
    id: UUID
    trip_id: UUID
    category_id: UUID


class VenueSelectionRecord(DomainRecord):
    id: UUID
    trip_id: UUID
    recommendation_id: UUID
    day_number: int
    time_block: str


class VenueSelectionDetail(VenueSelectionRecord):
    category_id: UUID
    neighborhood_scope: str
    place_id: UUID
    google_place_id: str
    place_name: str
    formatted_address: str
    lat: float
    lng: float


class ItineraryRecord(DomainRecord):
    id: UUID
    trip_id: UUID
    status: Literal["draft", "confirmed"]


class ItineraryDayRecord(DomainRecord):
    id: UUID
    itinerary_id: UUID
    day_number: int
    status: Literal["draft", "confirmed"]


class ItinerarySlotRecord(DomainRecord):
    id: UUID
    itinerary_day_id: UUID
    time_block: str
    slot_role: Literal["activity", "meal"]
    recommendation_id: UUID | None
