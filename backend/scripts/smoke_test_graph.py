"""Manual end-to-end smoke test — invoke through HITL pause, then resume."""
import asyncio
import uuid

from langgraph.types import Command

from app.graph.build import build_graph
from app.logging_config import configure_logging


async def main() -> None:
    configure_logging(level="INFO")
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "destination": "Tampa, Florida",
        "trip_length_days": 3,
        "categories": [],
        "candidates": [],
        "graded_candidates": [],
        "scored_recommendations": [],
        "user_selections": None,
        "itinerary": None,
        "research_iteration": 0,
        "replan_categories": [],
    }

    async with build_graph() as graph:
        print("--- Invoking graph (should pause at HITL) ---")
        result = await graph.ainvoke(initial_state, config)

        interrupts = result.get("__interrupt__")
        if not interrupts:
            print("NO INTERRUPT HIT — something is wrong, graph ran straight through.")
            print(result)
            return

        payload = interrupts[0].value
        recommendations = payload["recommendations"]
        print(f"Paused at HITL with {len(recommendations)} recommendations.")

        seen_ids = set()
        duplicate_ids = set()
        for rec in recommendations:
            if rec["id"] in seen_ids:
                duplicate_ids.add(rec["id"])
            seen_ids.add(rec["id"])
            print(
                f"  id={rec['id']} name={rec['name']!r} category={rec['category']!r} "
                f"score={rec['bourdain_score']} passed_guardrail={rec['passed_guardrail']}"
            )

        print(f"\nUnique ids: {len(seen_ids)} / Total recommendations: {len(recommendations)}")
        if duplicate_ids:
            print(f"DUPLICATE ids found ({len(duplicate_ids)}): {duplicate_ids}")

        selected_ids = [rec["id"] for rec in recommendations[:2]]
        print(f"\n--- Resuming with selections: {selected_ids} ---")
        final_result = await graph.ainvoke(Command(resume=selected_ids), config)

        itinerary = final_result.get("itinerary")
        print(f"\nFinal itinerary has {len(itinerary) if itinerary else 0} days.")
        for day in (itinerary or []):
            print(
                f"  day {day.day_number}: "
                f"neighborhood_focus={day.neighborhood_focus!r}, "
                f"activities={len(day.activities)}"
            )


if __name__ == "__main__":
    asyncio.run(main())