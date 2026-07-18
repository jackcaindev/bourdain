"""Persistence operations for itineraries, days, and slots."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from app.models.domain import ItineraryDayRecord, ItineraryRecord, ItinerarySlotRecord
from app.models.schemas import (
    PersistedItineraryDay,
    PersistedItineraryResponse,
    PersistedItinerarySlot,
    PersistedRecommendationView,
)
from app.services.vector_store import VectorStoreError, get_shared_pool


class ItineraryError(RuntimeError):
    """Raised when itinerary persistence fails."""


async def get_itinerary_with_details(
    trip_id: UUID,
) -> PersistedItineraryResponse | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            itinerary_row = await connection.fetchrow(
                "SELECT id, trip_id, status FROM itineraries WHERE trip_id = $1",
                trip_id,
            )
            if itinerary_row is None:
                return None
            rows = await connection.fetch(
                """
                SELECT
                    d.id AS day_id,
                    d.day_number,
                    d.status AS day_status,
                    s.id AS slot_id,
                    s.time_block,
                    s.slot_role,
                    r.id AS recommendation_id,
                    r.bourdain_score,
                    r.scoring_rationale,
                    c.name AS category_name,
                    p.name,
                    p.formatted_address,
                    p.lat,
                    p.lng,
                    p.google_types,
                    evidence.raw_content AS description
                FROM itinerary_days d
                LEFT JOIN itinerary_slots s ON s.itinerary_day_id = d.id
                LEFT JOIN recommendations r ON r.id = s.recommendation_id
                LEFT JOIN categories c ON c.id = r.category_id
                LEFT JOIN places p ON p.id = r.place_id
                LEFT JOIN LATERAL (
                    SELECT e.raw_content
                    FROM evidence e
                    WHERE e.place_id = r.place_id
                      AND e.research_run_id = r.research_run_id
                      AND e.source_type IN ('vector_store', 'web_search')
                    ORDER BY e.retrieved_at DESC, e.id DESC
                    LIMIT 1
                ) evidence ON true
                WHERE d.itinerary_id = $1
                ORDER BY
                    d.day_number,
                    CASE s.time_block
                        WHEN 'morning' THEN 1
                        WHEN 'afternoon' THEN 2
                        WHEN 'night' THEN 3
                        ELSE 4
                    END,
                    CASE s.slot_role WHEN 'activity' THEN 1 ELSE 2 END,
                    r.created_at,
                    s.id
                """,
                itinerary_row["id"],
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to read itinerary details.") from exc

    day_groups: dict[UUID, dict] = {}
    for row in rows:
        day = day_groups.setdefault(
            row["day_id"],
            {
                "day_number": row["day_number"],
                "status": row["day_status"],
                "slots": {},
            },
        )
        if row["slot_id"] is None:
            continue
        slot = day["slots"].setdefault(
            row["time_block"],
            {
                "time_block": row["time_block"],
                "activity": None,
                "meals": [],
            },
        )
        if row["recommendation_id"] is None:
            continue
        recommendation = PersistedRecommendationView(
            id=row["recommendation_id"],
            slot_id=row["slot_id"],
            name=row["name"],
            description=row["description"] or "",
            category_name=row["category_name"],
            bourdain_score=row["bourdain_score"],
            scoring_rationale=row["scoring_rationale"],
            formatted_address=row["formatted_address"],
            lat=row["lat"],
            lng=row["lng"],
            google_types=row["google_types"],
        )
        if row["slot_role"] == "activity":
            slot["activity"] = recommendation
        else:
            slot["meals"].append(recommendation)

    days = [
        PersistedItineraryDay(
            day_number=day["day_number"],
            status=day["status"],
            slots=[PersistedItinerarySlot(**slot) for slot in day["slots"].values()],
        )
        for day in day_groups.values()
    ]
    return PersistedItineraryResponse(
        trip_id=itinerary_row["trip_id"],
        status=itinerary_row["status"],
        days=days,
    )


async def get_itinerary_day_by_trip_and_day_number(
    trip_id: UUID, day_number: int
) -> ItineraryDayRecord | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT d.*
                FROM itinerary_days d
                JOIN itineraries i ON i.id = d.itinerary_id
                WHERE i.trip_id = $1 AND d.day_number = $2
                """,
                trip_id,
                day_number,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to read itinerary day.") from exc
    return ItineraryDayRecord.model_validate(dict(row)) if row is not None else None


async def get_itinerary_slot_by_id(slot_id: UUID) -> ItinerarySlotRecord | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM itinerary_slots WHERE id = $1", slot_id
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to read itinerary slot.") from exc
    return ItinerarySlotRecord.model_validate(dict(row)) if row is not None else None


async def get_slot_trip_and_category(slot_id: UUID) -> tuple[UUID, UUID] | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT i.trip_id, r.category_id
                FROM itinerary_slots s
                JOIN itinerary_days d ON d.id = s.itinerary_day_id
                JOIN itineraries i ON i.id = d.itinerary_id
                JOIN recommendations r ON r.id = s.recommendation_id
                WHERE s.id = $1
                """,
                slot_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to read slot ownership.") from exc
    return (row["trip_id"], row["category_id"]) if row is not None else None


async def update_itinerary_slot_recommendation(
    slot_id: UUID, recommendation_id: UUID
) -> ItinerarySlotRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE itinerary_slots
                SET recommendation_id = $2
                WHERE id = $1
                RETURNING *
                """,
                slot_id,
                recommendation_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to update itinerary slot.") from exc
    if row is None:
        raise ItineraryError(f"Itinerary slot {slot_id} was not found.")
    return ItinerarySlotRecord.model_validate(dict(row))


async def all_days_confirmed(itinerary_id: UUID) -> bool:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            confirmed = await connection.fetchval(
                """
                SELECT COALESCE(bool_and(status = 'confirmed'), false)
                FROM itinerary_days
                WHERE itinerary_id = $1
                """,
                itinerary_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to check itinerary day statuses.") from exc
    return bool(confirmed)


async def update_itinerary_status(
    itinerary_id: UUID, status: Literal["draft", "confirmed"]
) -> ItineraryRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE itineraries SET status = $2 WHERE id = $1 RETURNING *
                """,
                itinerary_id,
                status,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to update itinerary status.") from exc
    if row is None:
        raise ItineraryError(f"Itinerary {itinerary_id} was not found.")
    return ItineraryRecord.model_validate(dict(row))


async def create_itinerary(
    *,
    trip_id: UUID,
    status: Literal["draft", "confirmed"] = "draft",
) -> ItineraryRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO itineraries (trip_id, status)
                VALUES ($1, $2)
                RETURNING *
                """,
                trip_id,
                status,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to create itinerary.") from exc
    return ItineraryRecord.model_validate(dict(row))


async def create_itinerary_day(
    *,
    itinerary_id: UUID,
    day_number: int,
    status: Literal["draft", "confirmed"] = "draft",
) -> ItineraryDayRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO itinerary_days (itinerary_id, day_number, status)
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                itinerary_id,
                day_number,
                status,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to create itinerary day.") from exc
    return ItineraryDayRecord.model_validate(dict(row))


async def upsert_activity_slot(
    *,
    itinerary_day_id: UUID,
    time_block: str,
    recommendation_id: UUID,
) -> ItinerarySlotRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO itinerary_slots (
                    itinerary_day_id, time_block, slot_role, recommendation_id
                )
                VALUES ($1, $2, 'activity', $3)
                ON CONFLICT (itinerary_day_id, time_block)
                    WHERE slot_role = 'activity'
                DO UPDATE
                SET recommendation_id = EXCLUDED.recommendation_id
                RETURNING *
                """,
                itinerary_day_id,
                time_block,
                recommendation_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to upsert activity slot.") from exc
    return ItinerarySlotRecord.model_validate(dict(row))


async def create_meal_slot(
    *,
    itinerary_day_id: UUID,
    time_block: str,
    recommendation_id: UUID,
) -> ItinerarySlotRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO itinerary_slots (
                    itinerary_day_id, time_block, slot_role, recommendation_id
                )
                VALUES ($1, $2, 'meal', $3)
                RETURNING *
                """,
                itinerary_day_id,
                time_block,
                recommendation_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to create meal slot.") from exc
    return ItinerarySlotRecord.model_validate(dict(row))


async def confirm_itinerary_day(itinerary_day_id: UUID) -> ItineraryDayRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE itinerary_days
                SET status = 'confirmed'
                WHERE id = $1
                RETURNING *
                """,
                itinerary_day_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to confirm itinerary day.") from exc
    if row is None:
        raise ItineraryError(f"Itinerary day {itinerary_day_id} was not found.")
    return ItineraryDayRecord.model_validate(dict(row))
