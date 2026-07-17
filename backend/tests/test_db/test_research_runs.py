from app.db.research_runs import (
    complete_research_run,
    create_evidence,
    create_research_run,
)
from tests.test_db.helpers import DatabaseTestCase


class ResearchRunDatabaseTests(DatabaseTestCase):
    module_name = "research_runs"

    async def test_create_complete_run_and_create_evidence(self):
        trip_id = await self.insert_trip()
        category_id = await self.insert_category(trip_id)
        place_id = await self.insert_place()

        run = await create_research_run(
            trip_id=trip_id,
            category_id=category_id,
            trigger_reason="initial",
            iteration=1,
        )
        self.assertEqual(run.status, "running")
        self.assertEqual(run.iteration, 1)

        evidence = await create_evidence(
            place_id=place_id,
            research_run_id=run.id,
            source_type="web_search",
            raw_content="Independent reporting about the venue.",
        )
        self.assertEqual(evidence.research_run_id, run.id)

        completed = await complete_research_run(run.id)
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(completed.completed_at)
