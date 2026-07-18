"""Google Places REST boundary for destination and venue resolution."""

from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings


SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
CITY_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.location,places.types"
)
VENUE_FIELD_MASK = CITY_FIELD_MASK
LOCATION_BIAS_RADIUS_METERS = 50_000.0
VENUE_NAME_SIMILARITY_THRESHOLD = 0.65
PRO_TIER_FIELDS = frozenset(
    {
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.types",
    }
)

_shared_client: httpx.AsyncClient | None = None
_shared_client_lock = asyncio.Lock()


class PlacesError(RuntimeError):
    """Base exception for Google Places failures."""


class PlacesConnectionError(PlacesError):
    """Raised when Google Places cannot be reached."""


class PlacesQueryError(PlacesError):
    """Raised when Google Places cannot return a usable query result."""


class PlaceMatch(BaseModel):
    google_place_id: str
    name: str
    formatted_address: str
    lat: float
    lng: float
    google_types: list[str]


class CityResolution(BaseModel):
    status: Literal["resolved", "ambiguous"]
    match: PlaceMatch | None = None
    candidates: list[PlaceMatch] = Field(default_factory=list)


async def create_client() -> httpx.AsyncClient:
    """Create the process-wide Google Places HTTP client."""

    return httpx.AsyncClient()


async def get_shared_client() -> httpx.AsyncClient:
    """Return the shared client, creating it once for concurrent callers."""

    global _shared_client

    if _shared_client is not None:
        return _shared_client

    async with _shared_client_lock:
        if _shared_client is None:
            _shared_client = await create_client()
        return _shared_client


async def close_shared_client() -> None:
    """Close and clear the shared client, if initialized."""

    global _shared_client

    async with _shared_client_lock:
        if _shared_client is not None:
            try:
                await _shared_client.aclose()
            finally:
                _shared_client = None


async def search_text(
    query: str,
    *,
    field_mask: str,
    location_bias: tuple[float, float] | None = None,
) -> list[PlaceMatch]:
    """Run a Places Text Search and normalize its place results."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(field_mask, str) or not field_mask.strip():
        raise ValueError("field_mask must be a non-empty string")
    requested_fields = {field.strip() for field in field_mask.split(",")}
    unsupported_fields = requested_fields - PRO_TIER_FIELDS
    if unsupported_fields:
        raise ValueError(
            "field_mask contains unsupported fields: "
            f"{', '.join(sorted(unsupported_fields))}"
        )

    body: dict[str, object] = {"textQuery": query.strip()}
    if location_bias is not None:
        lat, lng = location_bias
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": LOCATION_BIAS_RADIUS_METERS,
            }
        }

    headers = {
        "X-Goog-Api-Key": get_settings().google_places_api_key.get_secret_value(),
        "X-Goog-FieldMask": field_mask,
    }

    try:
        client = await get_shared_client()
        response = await client.post(SEARCH_TEXT_URL, headers=headers, json=body)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise PlacesConnectionError("Failed to connect to Google Places.") from exc

    if not response.is_success:
        raise PlacesQueryError(
            f"Google Places query failed with status {response.status_code}."
        )

    try:
        payload = response.json()
        raw_places = payload.get("places", [])
        if not isinstance(raw_places, list):
            raise TypeError("Google Places response did not contain a places list")
        return [_parse_place(raw_place) for raw_place in raw_places]
    except (TypeError, KeyError, AttributeError, ValidationError, ValueError) as exc:
        raise PlacesQueryError("Google Places returned an invalid response.") from exc


async def resolve_city(destination_raw: str) -> CityResolution:
    """Resolve a raw city input or return candidates for user disambiguation."""

    matches = await search_text(destination_raw, field_mask=CITY_FIELD_MASK)
    if not matches:
        raise PlacesQueryError(f"No Google Places match found for {destination_raw!r}.")
    if len(matches) == 1:
        return CityResolution(status="resolved", match=matches[0])
    return CityResolution(status="ambiguous", candidates=matches[:5])


async def verify_venue(
    proposed_name: str,
    *,
    neighborhood_scope: str,
    city_name: str,
    location_bias: tuple[float, float],
) -> PlaceMatch | None:
    """Return the top plausible Places match for a proposed venue name."""

    matches = await search_text(
        f"{proposed_name}, {neighborhood_scope}, {city_name}",
        field_mask=VENUE_FIELD_MASK,
        location_bias=location_bias,
    )
    if not matches:
        return None

    match = matches[0]
    proposed_normalized = _normalize_name(proposed_name)
    matched_normalized = _normalize_name(match.name)
    if not proposed_normalized or not matched_normalized:
        return None
    similarity = SequenceMatcher(
        None, proposed_normalized, matched_normalized
    ).ratio()
    # 0.65 tolerates suffixes such as "Cafe" or a neighborhood qualifier while
    # rejecting unrelated position-zero results returned only because they are near.
    if similarity < VENUE_NAME_SIMILARITY_THRESHOLD:
        return None
    return match


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def _parse_place(raw_place: object) -> PlaceMatch:
    if not isinstance(raw_place, dict):
        raise TypeError("Place result must be an object")

    display_name = raw_place["displayName"]
    location = raw_place["location"]
    if not isinstance(display_name, dict) or not isinstance(location, dict):
        raise TypeError("Place name and location must be objects")

    return PlaceMatch(
        google_place_id=raw_place["id"],
        name=display_name["text"],
        formatted_address=raw_place["formattedAddress"],
        lat=location["latitude"],
        lng=location["longitude"],
        google_types=raw_place.get("types", []),
    )
