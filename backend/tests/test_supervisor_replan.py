from unittest import TestCase
from unittest.mock import patch

from app.graph.supervisor_replan import (
    SUPERVISOR_REPLAN_MODEL,
    supervisor_replan_check,
)
from app.models.schemas import Category, GradedCandidate


def _candidate(candidate_id: str, category: str) -> GradedCandidate:
    return GradedCandidate(
        id=candidate_id,
        name=candidate_id,
        category=category,
        description="Description",
        source="vector_store",
        raw_signal="Specific local evidence",
        relevance_score=0.4,
        authenticity_signal="Thin signal",
        confidence="low",
        needs_fallback=True,
    )


def _state(*, iteration: int = 0):
    return {
        "destination": "Porto",
        "trip_length_days": 3,
        "categories": [
            Category(name="Food", rationale="Local restaurants"),
            Category(name="Beaches", rationale="Coastal experiences"),
        ],
        "graded_candidates": [_candidate("food-one", "Food")],
        "research_iteration": iteration,
    }


class SupervisorReplanTests(TestCase):
    @patch("app.graph.supervisor_replan.call_forced_tool")
    def test_hard_cap_clears_transient_state_without_llm(self, forced_tool):
        result = supervisor_replan_check(_state(iteration=1))

        self.assertEqual(result, {"replan_categories": []})
        forced_tool.assert_not_called()

    @patch("app.graph.supervisor_replan.call_forced_tool")
    def test_no_replacements_still_increments_iteration(self, forced_tool):
        forced_tool.return_value = {"replacements": []}

        result = supervisor_replan_check(_state())

        self.assertEqual(
            result, {"replan_categories": [], "research_iteration": 1}
        )
        self.assertEqual(forced_tool.call_args.kwargs["model"], SUPERVISOR_REPLAN_MODEL)
        self.assertIn('"name": "Beaches"', forced_tool.call_args.kwargs["user_prompt"])
        self.assertIn('"graded_candidates": []', forced_tool.call_args.kwargs["user_prompt"])

    @patch("app.graph.supervisor_replan.call_forced_tool")
    def test_replaces_category_and_exposes_only_new_category(self, forced_tool):
        forced_tool.return_value = {
            "replacements": [
                {
                    "category_name": "Beaches",
                    "replacement": {
                        "name": "Port wine culture",
                        "rationale": "Cellars and taverns better fit Porto.",
                    },
                }
            ]
        }

        result = supervisor_replan_check(_state())

        self.assertEqual(
            [category.name for category in result["categories"]],
            ["Food", "Port wine culture"],
        )
        self.assertEqual(
            [category.name for category in result["replan_categories"]],
            ["Port wine culture"],
        )
        self.assertEqual(result["research_iteration"], 1)

    @patch("app.graph.supervisor_replan.call_forced_tool")
    def test_invalid_output_retries_once(self, forced_tool):
        forced_tool.side_effect = [
            {
                "replacements": [
                    {
                        "category_name": "Unknown",
                        "replacement": {"name": "History", "rationale": "Better fit"},
                    }
                ]
            },
            {"replacements": []},
        ]

        result = supervisor_replan_check(_state())

        self.assertEqual(result["research_iteration"], 1)
        self.assertEqual(forced_tool.call_count, 2)

    @patch(
        "app.graph.supervisor_replan.call_forced_tool",
        side_effect=RuntimeError("api down"),
    )
    def test_exhausted_failure_warns_and_proceeds(self, forced_tool):
        with self.assertLogs("app.graph.supervisor_replan", level="WARNING") as logs:
            result = supervisor_replan_check(_state())

        self.assertEqual(
            result, {"replan_categories": [], "research_iteration": 1}
        )
        self.assertEqual(forced_tool.call_count, 2)
        self.assertTrue(
            any("supervisor_replan_check_incomplete" in line for line in logs.output)
        )
