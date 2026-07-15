from contextlib import asynccontextmanager
import asyncio
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.logging_config import configure_logging
from app.services.vector_store import get_shared_pool
from app.graph.build import build_graph
from app.api.routes import router, _sessions, set_graph

TTL_SECONDS = 1800
SWEEP_INTERVAL = 60

async def _ttl_sweep():
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        now = time.monotonic()
        expired = [
            sid for sid, entry in _sessions.items()
            if now - entry.last_active > TTL_SECONDS
        ]
        for sid in expired:
            entry = _sessions.pop(sid, None)
            if entry:
                await entry.queue.put(None)

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await get_shared_pool()                    # warm pgvector pool
    async with build_graph() as graph:         # opens checkpointer, compiles once
        set_graph(graph)
        sweep_task = asyncio.create_task(_ttl_sweep())
        yield
        sweep_task.cancel()
        await asyncio.gather(sweep_task, return_exceptions=True)
    # build_graph()'s finally handles close_shared_pool() — don't call it here

app = FastAPI(title="Bourdain Brief", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
