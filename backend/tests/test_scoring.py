import asyncio
import threading
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from app.graph.scoring import (
    SCORING_MODEL,
    ScoringError,
    _score_candidate,
    scoring_node,
)
from app.models.schemas import GradedCandidate, ScoredRecommendation


def _candidate(candidate_id: str, name: str = "Cafe") -> GradedCandidate:
    return GradedCandidate(
        id=candidate_id,
        name=name,
        category="Food",
        description="A neighborhood cafe.",
        source="vector_store",
        raw_signal="Family-owned since 1972.",
        relevance_score=0.9,
        authenticity_signal="A longstanding neighborhood gathering place.",
        confidence="high",
        needs_fallback=False,
    )


def _score_output(score: int = 4) -> dict[str, object]:
    return {
        "bourdain_score": score,
        "scoring_rationale": "Local ownership and neighborhood history are evidenced.",
        "locally_owned_signal": "Family-owned since 1972.",
    }


class ScoreCandidateTests(TestCase):
    @patch("app.graph.scoring.call_forced_tool")
    def test_scores_one_candidate_with_sonnet(self, forced_tool):
        forced_tool.return_value = _score_output()

        result = _score_candidate(_candidate("one"))

        self.assertEqual(result.bourdain_score, 4)
        self.assertEqual(result.locally_owned_signal, "Family-owned since 1972.")
        self.assertFalse(result.passed_guardrail)
        self.assertIsNone(result.guardrail_note)
        self.assertEqual(forced_tool.call_args.kwargs["model"], SCORING_MODEL)

    @patch("app.graph.scoring.call_forced_tool")
    def test_validation_failure_retries_once(self, forced_tool):
        forced_tool.side_effect = [
            _score_output(score=6),
            {**_score_output(), "locally_owned_signal": None},
        ]

        result = _score_candidate(_candidate("one"))

        self.assertEqual(result.bourdain_score, 4)
        self.assertEqual(forced_tool.call_count, 2)

    @patch("app.graph.scoring.call_forced_tool", side_effect=RuntimeError("api down"))
    def test_exhausted_failure_raises_scoring_error(self, forced_tool):
        with self.assertRaisesRegex(ScoringError, "after one retry"):
            _score_candidate(_candidate("one"))

        self.assertEqual(forced_tool.call_count, 2)


class ScoringNodeTests(IsolatedAsyncioTestCase):
    @patch("app.graph.scoring._score_candidate")
    async def test_drops_failures_and_preserves_successes(self, score_candidate):
        first = _candidate("one", "First")
        second = _candidate("two", "Second")
        scored_first = first.model_dump() | _score_output() | {
            "passed_guardrail": False,
            "guardrail_note": None,
        }

        def score_by_id(candidate):
            if candidate.id == "one":
                return ScoredRecommendation(**scored_first)
            raise ScoringError("failed")

        score_candidate.side_effect = score_by_id

        with self.assertLogs("app.graph.scoring", level="WARNING") as logs:
            result = await scoring_node({"graded_candidates": [first, second]})

        self.assertEqual([item.id for item in result["scored_recommendations"]], ["one"])
        self.assertTrue(any("scoring_candidate_dropped" in line for line in logs.output))

    @patch("app.graph.scoring.call_forced_tool")
    async def test_scores_candidates_concurrently(self, forced_tool):
        barrier = threading.Barrier(2, timeout=1)

        def wait_for_other_call(**kwargs):
            barrier.wait()
            return _score_output()

        forced_tool.side_effect = wait_for_other_call

        result = await asyncio.wait_for(
            scoring_node(
                {"graded_candidates": [_candidate("one"), _candidate("two")]}
            ),
            timeout=2,
        )

        self.assertEqual(len(result["scored_recommendations"]), 2)
        self.assertEqual(forced_tool.call_count, 2)
