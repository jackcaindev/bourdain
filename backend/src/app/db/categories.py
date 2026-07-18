"""Persistence operations for research categories."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from app.models.domain import CategoryRecord
from app.services.vector_store import VectorStoreError, get_shared_pool


class CategoryError(RuntimeError):
    """Raised when a category persistence operation fails."""


async def create_category(
    *,
    trip_id: UUID,
    name: str,
    type: Literal["food", "activity"],
    estimated_duration_minutes: int,
    neighborhood_scope: str,
    source_drivers: list[str] | None = None,
    eligible_blocks: list[str] | None = None,
    status: Literal["candidate", "selected", "stale_replaced"] = "candidate",
    day_number: int | None = None,
    time_block: str | None = None,
) -> CategoryRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO categories (
                    trip_id, name, type, source_drivers, eligible_blocks,
                    estimated_duration_minutes, neighborhood_scope,
                    status, day_number, time_block
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING *
                """,
                trip_id,
                name,
                type,
                source_drivers or [],
                eligible_blocks or [],
                estimated_duration_minutes,
                neighborhood_scope,
                status,
                day_number,
                time_block,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise CategoryError("Failed to create category.") from exc
    return CategoryRecord.model_validate(dict(row))


async def get_categories_by_trip_id(trip_id: UUID) -> list[CategoryRecord]:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT * FROM categories WHERE trip_id = $1 ORDER BY created_at, id",
                trip_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise CategoryError("Failed to read categories for trip.") from exc
    return [CategoryRecord.model_validate(dict(row)) for row in rows]


async def get_category_by_id(category_id: UUID) -> CategoryRecord | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM categories WHERE id = $1", category_id
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise CategoryError("Failed to read category.") from exc
    return CategoryRecord.model_validate(dict(row)) if row is not None else None


async def mark_categories_selected(
    trip_id: UUID, category_ids: list[UUID]
) -> None:
    if not category_ids:
        return
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE categories
                SET status = 'selected'
                WHERE trip_id = $1 AND id = ANY($2::uuid[])
                """,
                trip_id,
                category_ids,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise CategoryError("Failed to mark categories selected.") from exc
