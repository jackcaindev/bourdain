"""Persistence operations for research runs and their evidence."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from app.models.domain import EvidenceRecord, ResearchRunRecord
from app.services.vector_store import VectorStoreError, get_shared_pool


class ResearchRunError(RuntimeError):
    """Raised when research-run persistence fails."""


async def create_research_run(
    *,
    trip_id: UUID,
    category_id: UUID,
    trigger_reason: Literal[
        "initial", "crag_fallback", "supervisor_replan", "on_demand"
    ],
    iteration: int = 0,
) -> ResearchRunRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO research_runs (
                    trip_id, category_id, trigger_reason, iteration, status
                )
                VALUES ($1, $2, $3, $4, 'running')
                RETURNING *
                """,
                trip_id,
                category_id,
                trigger_reason,
                iteration,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ResearchRunError("Failed to create research run.") from exc
    return ResearchRunRecord.model_validate(dict(row))


async def complete_research_run(research_run_id: UUID) -> ResearchRunRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE research_runs
                SET status = 'completed', completed_at = now()
                WHERE id = $1
                RETURNING *
                """,
                research_run_id,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ResearchRunError("Failed to complete research run.") from exc
    if row is None:
        raise ResearchRunError(f"Research run {research_run_id} was not found.")
    return ResearchRunRecord.model_validate(dict(row))


async def create_evidence(
    *,
    place_id: UUID,
    research_run_id: UUID,
    source_type: Literal["vector_store", "web_search", "places_api"],
    raw_content: str,
) -> EvidenceRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO evidence (
                    place_id, research_run_id, source_type, raw_content
                )
                VALUES ($1, $2, $3, $4)
                RETURNING *
                """,
                place_id,
                research_run_id,
                source_type,
                raw_content,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise ResearchRunError("Failed to create research evidence.") from exc
    return EvidenceRecord.model_validate(dict(row))
