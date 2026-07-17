"""Persistence operations for scored recommendations."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from app.models.domain import RecommendationRecord
from app.services.vector_store import VectorStoreError, get_shared_pool


class RecommendationError(RuntimeError):
    """Raised when recommendation persistence fails."""


async def create_recommendation(
    *,
    trip_id: UUID,
    category_id: UUID,
    research_run_id: UUID,
    place_id: UUID,
    relevance_score: float,
    authenticity_signal: str,
    confidence: Literal["low", "medium", "high"],
    needs_fallback: bool,
    bourdain_score: int,
    scoring_rationale: str,
    passed_guardrail: bool,
    locally_owned_signal: str | None = None,
    guardrail_note: str | None = None,
) -> RecommendationRecord:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO recommendations (
                    trip_id, category_id, research_run_id, place_id,
                    relevance_score, authenticity_signal, confidence,
                    needs_fallback, bourdain_score, scoring_rationale,
                    locally_owned_signal, passed_guardrail, guardrail_note
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10, $11, $12, $13
                )
                RETURNING *
                """,
                trip_id,
                category_id,
                research_run_id,
                place_id,
                relevance_score,
                authenticity_signal,
                confidence,
                needs_fallback,
                bourdain_score,
                scoring_rationale,
                locally_owned_signal,
                passed_guardrail,
                guardrail_note,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise RecommendationError("Failed to create recommendation.") from exc
    return RecommendationRecord.model_validate(dict(row))


async def get_recommendation_by_id(
    recommendation_id: UUID,
) -> RecommendationRecord | None:
    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM recommendations WHERE id = $1", recommendation_id
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, VectorStoreError) as exc:
        raise RecommendationError("Failed to read recommendation.") from exc
    return RecommendationRecord.model_validate(dict(row)) if row is not None else None
