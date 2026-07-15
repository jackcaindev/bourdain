"""Postgres-backed cache for scored recommendations by city and category."""

from __future__ import annotations

import json
import logging

from pydantic import TypeAdapter

from app.models.schemas import ScoredRecommendation
from app.services.vector_store import get_shared_pool


logger = logging.getLogger(__name__)

_RECOMMENDATIONS_ADAPTER = TypeAdapter(list[ScoredRecommendation])


class CategoryCacheError(RuntimeError):
    """Raised when the category cache cannot be read or written safely."""


async def get_cached_recommendations(
    city_slug: str, category_name: str
) -> list[ScoredRecommendation] | None:
    """Return an unexpired cached recommendation list, or ``None`` on a miss."""

    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT recommendations::text AS recommendations
                FROM category_cache
                WHERE city_slug = $1
                  AND category_name = $2
                  AND expires_at > now()
                """,
                city_slug,
                category_name,
            )
    except Exception as exc:
        logger.exception(
            "category_cache_read_failed",
            extra={"city_slug": city_slug, "category_name": category_name},
        )
        raise CategoryCacheError("Failed to read the category cache.") from exc

    if row is None:
        logger.debug(
            "category_cache_miss",
            extra={"city_slug": city_slug, "category_name": category_name},
        )
        return None

    try:
        recommendations = json.loads(row["recommendations"])
        return _RECOMMENDATIONS_ADAPTER.validate_python(recommendations)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.exception(
            "category_cache_deserialization_failed",
            extra={"city_slug": city_slug, "category_name": category_name},
        )
        raise CategoryCacheError(
            "Cached recommendations contained invalid data."
        ) from exc


async def write_category_cache(
    city_slug: str,
    category_name: str,
    recommendations: list[ScoredRecommendation],
) -> None:
    """Upsert recommendations with a fresh 90-day expiration."""

    recommendations_json = json.dumps(
        [recommendation.model_dump(mode="json") for recommendation in recommendations]
    )

    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO category_cache (
                    city_slug,
                    category_name,
                    recommendations,
                    expires_at
                )
                VALUES ($1, $2, $3::jsonb, now() + INTERVAL '90 days')
                ON CONFLICT (city_slug, category_name)
                DO UPDATE SET
                    recommendations = EXCLUDED.recommendations,
                    expires_at = now() + INTERVAL '90 days'
                """,
                city_slug,
                category_name,
                recommendations_json,
            )
    except Exception as exc:
        logger.exception(
            "category_cache_write_failed",
            extra={
                "city_slug": city_slug,
                "category_name": category_name,
                "recommendation_count": len(recommendations),
            },
        )
        raise CategoryCacheError("Failed to write the category cache.") from exc
