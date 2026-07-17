"""Persistence operations for category and venue selections."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from app.models.domain import (
    CategorySelectionRecord,
    VenueSelectionDetail,
    VenueSelectionRecord,
)
from app.services.vector_store import VectorStoreError, get_shared_pool


class SelectionError(RuntimeError):
    """Raised when selection persistence fails."""


async def create_category_selection(
    *, trip_id: UUID, category_id: UUID
) -> CategorySelectionRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO category_selections (trip_id, category_id)
                VALUES ($1, $2)
                RETURNING *
                """,
                trip_id,
                category_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise SelectionError("Failed to create category selection.") from exc
    return CategorySelectionRecord.model_validate(dict(row))


async def create_venue_selection(
    *,
    trip_id: UUID,
    recommendation_id: UUID,
    day_number: int,
    time_block: str,
) -> VenueSelectionRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO venue_selections (
                    trip_id, recommendation_id, day_number, time_block
                )
                VALUES ($1, $2, $3, $4)
                RETURNING *
                """,
                trip_id,
                recommendation_id,
                day_number,
                time_block,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise SelectionError("Failed to create venue selection.") from exc
    return VenueSelectionRecord.model_validate(dict(row))


async def get_venue_selections_by_trip_id(
    trip_id: UUID,
) -> list[VenueSelectionDetail]:
    """Return selections with place and neighborhood data for cross-day dedup."""

    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT
                    vs.id, vs.trip_id, vs.recommendation_id,
                    vs.day_number, vs.time_block,
                    r.category_id,
                    c.neighborhood_scope,
                    p.id AS place_id,
                    p.google_place_id,
                    p.name AS place_name,
                    p.formatted_address,
                    p.lat,
                    p.lng
                FROM venue_selections AS vs
                JOIN recommendations AS r ON r.id = vs.recommendation_id
                JOIN categories AS c ON c.id = r.category_id
                JOIN places AS p ON p.id = r.place_id
                WHERE vs.trip_id = $1
                ORDER BY vs.day_number, vs.time_block, vs.id
                """,
                trip_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise SelectionError("Failed to read venue selections.") from exc
    return [VenueSelectionDetail.model_validate(dict(row)) for row in rows]
