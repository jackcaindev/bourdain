"""Persistence operations for trips."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from app.models.domain import TripRecord
from app.services.vector_store import VectorStoreError, get_shared_pool


class TripError(RuntimeError):
    """Raised when a trip persistence operation fails."""


async def create_trip(
    *,
    destination_raw: str,
    destination_place_id: str,
    destination_formatted: str,
    trip_length_days: int,
    session_id: str,
    activity_drivers: list[str] | None = None,
    food_selections: list[str] | None = None,
    time_blocks: list[str] | None = None,
    status: Literal[
        "gathering_categories", "researching", "reviewing", "confirmed"
    ] = "gathering_categories",
) -> TripRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO trips (
                    destination_raw, destination_place_id, destination_formatted,
                    trip_length_days, activity_drivers, food_selections,
                    time_blocks, status, session_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING *
                """,
                destination_raw,
                destination_place_id,
                destination_formatted,
                trip_length_days,
                activity_drivers or [],
                food_selections or [],
                time_blocks or [],
                status,
                session_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise TripError("Failed to create trip.") from exc
    return TripRecord.model_validate(dict(row))


async def get_trip_by_id(trip_id: UUID) -> TripRecord | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow("SELECT * FROM trips WHERE id = $1", trip_id)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise TripError("Failed to read trip by id.") from exc
    return TripRecord.model_validate(dict(row)) if row is not None else None


async def get_trip_by_session_id(session_id: str) -> TripRecord | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM trips WHERE session_id = $1", session_id
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise TripError("Failed to read trip by session id.") from exc
    return TripRecord.model_validate(dict(row)) if row is not None else None
