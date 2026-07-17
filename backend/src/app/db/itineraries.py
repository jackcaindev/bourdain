"""Persistence operations for itineraries, days, and slots."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from app.models.domain import ItineraryDayRecord, ItineraryRecord, ItinerarySlotRecord
from app.services.vector_store import VectorStoreError, get_shared_pool


class ItineraryError(RuntimeError):
    """Raised when itinerary persistence fails."""


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


async def upsert_itinerary_slot(
    *,
    itinerary_day_id: UUID,
    time_block: str,
    recommendation_id: UUID | None = None,
) -> ItinerarySlotRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO itinerary_slots (
                    itinerary_day_id, time_block, recommendation_id
                )
                VALUES ($1, $2, $3)
                ON CONFLICT (itinerary_day_id, time_block) DO UPDATE
                SET recommendation_id = EXCLUDED.recommendation_id
                RETURNING *
                """,
                itinerary_day_id,
                time_block,
                recommendation_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ItineraryError("Failed to upsert itinerary slot.") from exc
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
