import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.services import vector_store


class SharedPoolTests(IsolatedAsyncioTestCase):
    async def test_get_shared_pool_creates_once_for_concurrent_callers(self):
        pool = AsyncMock()

        with (
            patch.object(vector_store, "_shared_pool", None),
            patch.object(
                vector_store, "create_pool", new=AsyncMock(return_value=pool)
            ) as create_pool,
        ):
            pools = await asyncio.gather(
                *(vector_store.get_shared_pool() for _ in range(20))
            )

        self.assertTrue(all(result is pool for result in pools))
        create_pool.assert_awaited_once()

    async def test_close_shared_pool_closes_and_clears_existing_pool(self):
        pool = AsyncMock()

        with patch.object(vector_store, "_shared_pool", pool):
            await vector_store.close_shared_pool()

            pool.close.assert_awaited_once()
            self.assertIsNone(vector_store._shared_pool)
