"""Persistence operations for deduplicated places."""

from __future__ import annotations

import asyncpg

from app.models.domain import PlaceRecord
from app.services.vector_store import VectorStoreError, get_shared_pool


class PlaceError(RuntimeError):
    """Raised when a place persistence operation fails."""


async def get_or_create_place(
    *,
    google_place_id: str,
    name: str,
    formatted_address: str,
    lat: float,
    lng: float,
    google_types: list[str] | None = None,
) -> PlaceRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO places (
                    google_place_id, name, formatted_address, lat, lng, google_types
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (google_place_id) DO UPDATE
                SET google_place_id = EXCLUDED.google_place_id
                RETURNING *
                """,
                google_place_id,
                name,
                formatted_address,
                lat,
                lng,
                google_types or [],
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise PlaceError("Failed to get or create place.") from exc
    return PlaceRecord.model_validate(dict(row))
