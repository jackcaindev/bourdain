"""Manual end-to-end smoke test — invoke through both HITL pauses, then resume."""
import asyncio
import uuid

from langgraph.types import Command

from app.graph.build import build_graph
from app.logging_config import configure_logging
from app.models.schemas import CategoryListPayload, HitlPayload


async def main() -> None:
    configure_logging(level="INFO")
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "destination": "Tampa, Florida",
        "city_slug": "",
        "trip_length_days": 3,
        "categories": [],
        "selected_categories": None,
        "research_iteration": 0,
        "replan_categories": [],
        "scored_recommendations": [],
        "user_selections": None,
        "itinerary": None,
    }

    async with build_graph() as graph:
        print("--- Invoking graph (should pause at category selection) ---")
        result = await graph.ainvoke(initial_state, config)

        interrupts = result.get("__interrupt__")
        if not interrupts:
            print("NO INTERRUPT HIT — something is wrong, graph ran straight through.")
            print(result)
            return

        payload = interrupts[0].value
        if not isinstance(payload, CategoryListPayload):
            print("UNEXPECTED INTERRUPT — something is wrong.")
            print(result)
            return

        categories = payload.categories
        print(f"Paused at category selection with {len(categories)} categories.")
        for category in categories:
            print(f"  name={category.name!r} rationale={category.rationale!r}")

        selected_categories = [category.name for category in categories]
        print(f"\n--- Resuming with categories: {selected_categories} ---")
        result = await graph.ainvoke(Command(resume=selected_categories), config)

        interrupts = result.get("__interrupt__")
        if not interrupts:
            print("NO SECOND INTERRUPT HIT — something is wrong, graph ran straight through.")
            print(result)
            return

        payload = interrupts[0].value
        if not isinstance(payload, HitlPayload):
            print("UNEXPECTED INTERRUPT — something is wrong.")
            print(result)
            return

        recommendations = payload.recommendations
        print(f"Paused at HITL with {len(recommendations)} recommendations.")

        seen_ids = set()
        duplicate_ids = set()
        for rec in recommendations:
            if rec.id in seen_ids:
                duplicate_ids.add(rec.id)
            seen_ids.add(rec.id)
            print(
                f"  id={rec.id} name={rec.name!r} category={rec.category!r} "
                f"score={rec.bourdain_score} passed_guardrail={rec.passed_guardrail}"
            )

        print(f"\nUnique ids: {len(seen_ids)} / Total recommendations: {len(recommendations)}")
        if duplicate_ids:
            print(f"DUPLICATE ids found ({len(duplicate_ids)}): {duplicate_ids}")

        selected_ids = [rec.id for rec in recommendations[:2]]
        print(f"\n--- Resuming with selections: {selected_ids} ---")
        final_result = await graph.ainvoke(Command(resume=selected_ids), config)

        if final_result.get("__interrupt__"):
            print("UNEXPECTED INTERRUPT — something is wrong.")
            print(final_result)
            return

        itinerary = final_result["itinerary"]
        print(f"\nFinal itinerary has {len(itinerary) if itinerary else 0} days.")
        for day in (itinerary or []):
            print(
                f"  day {day.day_number}: "
                f"neighborhood_focus={day.neighborhood_focus!r}, "
                f"activities={len(day.activities)}"
            )


if __name__ == "__main__":
    asyncio.run(main())
