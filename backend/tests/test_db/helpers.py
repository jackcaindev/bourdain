"""Real-Postgres transaction fixtures for persistence integration tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg

from app.config import get_settings


class TransactionPool:
    """Pool-shaped wrapper that keeps repository work on one test transaction."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class DatabaseTestCase(IsolatedAsyncioTestCase):
    module_name: str

    async def asyncSetUp(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=get_settings().database_url.get_secret_value(), min_size=1, max_size=1
        )
        self.connection = await self.pool.acquire()
        self.transaction = self.connection.transaction()
        await self.transaction.start()
        self.transaction_pool = TransactionPool(self.connection)
        self.pool_patcher = patch(
            f"app.db.{self.module_name}.get_shared_pool",
            new=AsyncMock(return_value=self.transaction_pool),
        )
        self.pool_patcher.start()

    async def asyncTearDown(self) -> None:
        self.pool_patcher.stop()
        await self.transaction.rollback()
        await self.pool.release(self.connection)
        await self.pool.close()

    async def insert_trip(self) -> UUID:
        return await self.connection.fetchval(
            """
            INSERT INTO trips (
                destination_raw, destination_place_id, destination_formatted,
                destination_lat, destination_lng,
                trip_length_days, status, session_id
            )
            VALUES ($1, $2, $3, 40.0, -74.0, 3, 'gathering_categories', $4)
            RETURNING id
            """,
            "Test City",
            f"destination-{uuid4()}",
            "Test City, Test Country",
            f"session-{uuid4()}",
        )

    async def insert_place(self) -> UUID:
        return await self.connection.fetchval(
            """
            INSERT INTO places (
                google_place_id, name, formatted_address, lat, lng
            )
            VALUES ($1, 'Corner Cafe', '1 Test Street', 40.1, -73.9)
            RETURNING id
            """,
            f"place-{uuid4()}",
        )

    async def insert_category(self, trip_id: UUID) -> UUID:
        return await self.connection.fetchval(
            """
            INSERT INTO categories (
                trip_id, name, type, estimated_duration_minutes,
                neighborhood_scope, status
            )
            VALUES ($1, 'Neighborhood lunch', 'food', 60, 'Old Town', 'candidate')
            RETURNING id
            """,
            trip_id,
        )

    async def insert_research_run(self, trip_id: UUID, category_id: UUID) -> UUID:
        return await self.connection.fetchval(
            """
            INSERT INTO research_runs (
                trip_id, category_id, trigger_reason, status
            )
            VALUES ($1, $2, 'initial', 'running')
            RETURNING id
            """,
            trip_id,
            category_id,
        )

    async def insert_recommendation(
        self, trip_id: UUID, category_id: UUID, research_run_id: UUID, place_id: UUID
    ) -> UUID:
        return await self.connection.fetchval(
            """
            INSERT INTO recommendations (
                trip_id, category_id, research_run_id, place_id,
                relevance_score, authenticity_signal, confidence,
                needs_fallback, bourdain_score, scoring_rationale,
                passed_guardrail
            )
            VALUES ($1, $2, $3, $4, 0.9, 'local institution', 'high',
                    false, 5, 'Strong fit', true)
            RETURNING id
            """,
            trip_id,
            category_id,
            research_run_id,
            place_id,
        )
