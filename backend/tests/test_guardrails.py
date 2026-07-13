from unittest import TestCase
from unittest.mock import patch

from app.graph.guardrails import GUARDRAIL_MODEL, guardrail_node
from app.models.schemas import ScoredRecommendation


def _recommendation(
    recommendation_id: str,
    *,
    raw_signal: str = "Family-owned since 1972 and popular with neighborhood regulars.",
) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=recommendation_id,
        name=f"Cafe {recommendation_id}",
        category="Food",
        description="A neighborhood cafe.",
        source="vector_store",
        raw_signal=raw_signal,
        relevance_score=0.9,
        authenticity_signal="A longstanding neighborhood gathering place.",
        confidence="high",
        needs_fallback=False,
        bourdain_score=4,
        scoring_rationale="Local ownership and neighborhood history are evidenced.",
        locally_owned_signal="Family-owned since 1972.",
        passed_guardrail=False,
        guardrail_note=None,
    )


def _result(recommendation_id: str, *, grounded: bool = True) -> dict[str, object]:
    return {
        "recommendation_id": recommendation_id,
        "is_grounded": grounded,
        "guardrail_note": None if grounded else "The claimed history is unsupported.",
    }


class GuardrailNodeTests(TestCase):
    @patch("app.graph.guardrails.call_forced_tool")
    def test_stage_1_flags_short_signal_and_excludes_it_from_batch(self, forced_tool):
        forced_tool.return_value = {"results": [_result("good")]}

        result = guardrail_node(
            {
                "scored_recommendations": [
                    _recommendation("short", raw_signal="   "),
                    _recommendation("good"),
                ]
            }
        )["scored_recommendations"]

        self.assertEqual([item.id for item in result], ["short", "good"])
        self.assertFalse(result[0].passed_guardrail)
        self.assertEqual(
            result[0].guardrail_note,
            "insufficient raw_signal evidence: 0 characters",
        )
        self.assertTrue(result[1].passed_guardrail)
        user_prompt = forced_tool.call_args.kwargs["user_prompt"]
        self.assertNotIn('"id": "short"', user_prompt)
        self.assertIn('"id": "good"', user_prompt)
        self.assertEqual(forced_tool.call_args.kwargs["model"], GUARDRAIL_MODEL)

    @patch("app.graph.guardrails.call_forced_tool")
    def test_matches_reordered_results_by_id_and_preserves_flags(self, forced_tool):
        forced_tool.return_value = {
            "results": [_result("two", grounded=False), _result("one")]
        }

        result = guardrail_node(
            {"scored_recommendations": [_recommendation("one"), _recommendation("two")]}
        )["scored_recommendations"]

        self.assertTrue(result[0].passed_guardrail)
        self.assertIsNone(result[0].guardrail_note)
        self.assertFalse(result[1].passed_guardrail)
        self.assertEqual(result[1].guardrail_note, "The claimed history is unsupported.")
        self.assertEqual(forced_tool.call_count, 1)

    @patch("app.graph.guardrails.call_forced_tool")
    def test_validation_failure_retries_once(self, forced_tool):
        forced_tool.side_effect = [
            {"results": [_result("unknown")]},
            {"results": [_result("one")]},
        ]

        result = guardrail_node(
            {"scored_recommendations": [_recommendation("one")]}
        )["scored_recommendations"]

        self.assertTrue(result[0].passed_guardrail)
        self.assertEqual(forced_tool.call_count, 2)

    @patch("app.graph.guardrails.call_forced_tool", side_effect=RuntimeError("api down"))
    def test_exhausted_check_flags_candidates_as_incomplete(self, forced_tool):
        with self.assertLogs("app.graph.guardrails", level="WARNING") as logs:
            result = guardrail_node(
                {"scored_recommendations": [_recommendation("one")]}
            )["scored_recommendations"]

        self.assertFalse(result[0].passed_guardrail)
        self.assertIn("could not complete", result[0].guardrail_note)
        self.assertEqual(forced_tool.call_count, 2)
        self.assertTrue(any("guardrail_stage_2_incomplete" in line for line in logs.output))

    @patch("app.graph.guardrails.call_forced_tool")
    def test_all_stage_1_failures_skip_llm(self, forced_tool):
        result = guardrail_node(
            {"scored_recommendations": [_recommendation("one", raw_signal="too short")]}
        )["scored_recommendations"]

        forced_tool.assert_not_called()
        self.assertFalse(result[0].passed_guardrail)

    @patch("app.graph.guardrails.call_forced_tool")
    def test_duplicate_or_missing_ids_never_silently_pass(self, forced_tool):
        forced_tool.return_value = {
            "results": [_result("one"), _result("one")]
        }

        result = guardrail_node(
            {"scored_recommendations": [_recommendation("one"), _recommendation("two")]}
        )["scored_recommendations"]

        self.assertEqual(forced_tool.call_count, 2)
        self.assertTrue(all(not item.passed_guardrail for item in result))
        self.assertTrue(all("could not complete" in item.guardrail_note for item in result))
