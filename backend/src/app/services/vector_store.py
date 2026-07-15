"""Async pgvector-backed store for local-guide snippets."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from pgvector.asyncpg import register_vector

from app.config import get_settings


EMBEDDING_DIMENSIONS = 1536
TABLE_NAME = "local_guide_snippets"

_shared_pool: asyncpg.Pool | None = None
_shared_pool_lock = asyncio.Lock()


class VectorStoreError(RuntimeError):
    """Base exception for vector-store failures."""


class VectorStoreConnectionError(VectorStoreError):
    """Raised when the vector store cannot connect or initialize codecs."""


class VectorStoreQueryError(VectorStoreError):
    """Raised when a vector-store query fails."""


@dataclass(frozen=True)
class VectorSearchResult:
    """Nearest-neighbor row returned by the vector store."""

    id: UUID
    name: str
    content: str
    category: str
    metadata: dict[str, Any]
    distance: float


def _validate_embedding(embedding: list[float]) -> None:
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedding must contain {EMBEDDING_DIMENSIONS} dimensions, "
            f"got {len(embedding)}"
        )


async def create_pool(database_url: str | None = None) -> asyncpg.Pool:
    """Create an asyncpg pool with pgvector codecs registered on each connection."""

    dsn = database_url or get_settings().database_url.get_secret_value()

    async def init_connection(connection: asyncpg.Connection) -> None:
        await register_vector(connection)

    try:
        return await asyncpg.create_pool(dsn=dsn, init=init_connection)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        raise VectorStoreConnectionError(
            "Failed to connect to the vector store database."
        ) from exc


async def get_shared_pool() -> asyncpg.Pool:
    """Return the process-wide vector-store pool, creating it on first use."""

    global _shared_pool

    if _shared_pool is not None:
        return _shared_pool

    async with _shared_pool_lock:
        if _shared_pool is None:
            _shared_pool = await create_pool()
        return _shared_pool


async def close_shared_pool() -> None:
    """Close and clear the process-wide vector-store pool, if initialized."""

    global _shared_pool

    async with _shared_pool_lock:
        if _shared_pool is not None:
            try:
                await _shared_pool.close()
            finally:
                _shared_pool = None


async def insert_candidate(
    pool: asyncpg.Pool,
    *,
    name: str,
    content: str,
    category: str,
    city_slug: str,
    embedding: list[float],
    metadata: dict[str, Any] | None = None,
    candidate_id: UUID | None = None,
) -> UUID:
    """Insert a local-guide candidate snippet and return its id."""

    _validate_embedding(embedding)
    row_id = candidate_id or uuid4()
    metadata_json = json.dumps(metadata or {})

    try:
        async with pool.acquire() as connection:
            return await connection.fetchval(
                f"""
                INSERT INTO {TABLE_NAME} (
                    id, name, content, category, city_slug, metadata, embedding
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                RETURNING id
                """,
                row_id,
                name,
                content,
                category,
                city_slug,
                metadata_json,
                embedding,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        raise VectorStoreQueryError("Failed to insert vector-store candidate.") from exc


async def query_nearest_neighbors(
    pool: asyncpg.Pool,
    *,
    query_embedding: list[float],
    city_slug: str,
    top_k: int = 5,
) -> list[VectorSearchResult]:
    """Return the top-k nearest snippets ordered by pgvector L2 distance."""

    _validate_embedding(query_embedding)
    if top_k <= 0:
        raise ValueError("top_k must be greater than 0")

    try:
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT
                    id,
                    name,
                    content,
                    category,
                    metadata::text AS metadata,
                    embedding <-> $1 AS distance
                FROM {TABLE_NAME}
                WHERE city_slug = $2
                ORDER BY embedding <-> $1
                LIMIT $3
                """,
                query_embedding,
                city_slug,
                top_k,
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        raise VectorStoreQueryError("Failed to query nearest neighbors.") from exc

    return [
        VectorSearchResult(
            id=row["id"],
            name=row["name"],
            content=row["content"],
            category=row["category"],
            metadata=json.loads(row["metadata"]),
            distance=float(row["distance"]),
        )
        for row in rows
    ]
