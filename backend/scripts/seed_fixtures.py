"""One-off fixture seeding for a manual graph smoke test — not part of the app."""
import asyncio
from app.services.vector_store import create_pool, insert_candidate

FIXTURES = [
    ("Ferg's Corner Diner", "A family-run diner open since the 1960s, known locally for its counter seating and regulars.", "food"),
    ("Riverside Warehouse District", "A converted industrial block now home to independent artist studios.", "neighborhoods"),
    ("Old Town Night Market", "A weekly market run by local vendors, not aimed at tourists.", "markets"),
    ("Harbor Walk", "A quiet waterfront path popular with residents in the early morning.", "activities"),
    ("The Blue Anchor", "An independently owned dive bar with live local music on weekends.", "nightlife"),
]

async def main() -> None:
    pool = await create_pool()
    try:
        zero_vector = [0.0] * 1536
        for name, content, category in FIXTURES:
            await insert_candidate(
                pool,
                name=name,
                content=content,
                category=category,
                embedding=zero_vector,
                metadata={"source_url": None},
            )
        print(f"Seeded {len(FIXTURES)} fixture rows.")
    finally:
        await pool.close()

if __name__ == "__main__":
    asyncio.run(main())