"""Persistence helpers for destination-specific city profiles."""

from __future__ import annotations

import json
import logging
import re

import asyncpg
from pydantic import TypeAdapter, ValidationError

from app.models.schemas import Category
from app.services.vector_store import VectorStoreError, get_shared_pool


logger = logging.getLogger(__name__)

_CATEGORY_LIST_ADAPTER = TypeAdapter(list[Category])


class CityProfileError(RuntimeError):
    """Raised when a city profile cannot be read or written."""


def normalize_city_slug(destination: str) -> str:
    """Return a lowercase, punctuation-free slug for a destination name."""

    without_punctuation = "".join(
        character
        for character in destination.lower()
        if character.isalnum() or character.isspace()
    )
    return re.sub(r"\s+", "-", without_punctuation.strip())


async def get_city_profile(city_slug: str) -> list[Category] | None:
    """Return the stored categories for a city, or ``None`` when absent."""

    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT categories::text AS categories
                FROM city_profiles
                WHERE city_slug = $1
                """,
                city_slug,
            )
    except (
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
        OSError,
        VectorStoreError,
    ) as exc:
        logger.exception(
            "Failed to read city profile.",
            extra={"city_slug": city_slug},
        )
        raise CityProfileError(f"Failed to read city profile for {city_slug!r}.") from exc

    if row is None:
        return None

    try:
        categories = json.loads(row["categories"])
        return _CATEGORY_LIST_ADAPTER.validate_python(categories)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        logger.exception(
            "Stored city profile categories are invalid.",
            extra={"city_slug": city_slug},
        )
        raise CityProfileError(
            f"Stored city profile categories are invalid for {city_slug!r}."
        ) from exc


async def save_city_profile(
    city_slug: str,
    city_name: str,
    categories: list[Category],
) -> None:
    """Insert or update a city's stored research categories."""

    categories_json = json.dumps(
        [category.model_dump(mode="json") for category in categories]
    )

    try:
        pool = await get_shared_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO city_profiles (city_slug, city_name, categories)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (city_slug) DO UPDATE
                SET categories = EXCLUDED.categories,
                    updated_at = NOW()
                """,
                city_slug,
                city_name,
                categories_json,
            )
    except (
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
        OSError,
        VectorStoreError,
    ) as exc:
        logger.exception(
            "Failed to save city profile.",
            extra={"city_slug": city_slug},
        )
        raise CityProfileError(f"Failed to save city profile for {city_slug!r}.") from exc
