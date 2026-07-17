from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from app.graph.scoring import (
    SCORING_MODEL,
    ScoringError,
    _score_candidates,
    scoring_node,
)
from app.models.schemas import GradedCandidate


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


def _score(candidate_id: str, score: int = 4) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "bourdain_score": score,
        "scoring_rationale": (
            "Local ownership and neighborhood history are evidenced."
        ),
        "locally_owned_signal": "Family-owned since 1972.",
    }


class ScoreCandidatesTests(TestCase):
    def setUp(self):
        self.candidates = [
            _candidate("first-id", "First"),
            _candidate("second-id", "Second"),
        ]

    @patch("app.graph.scoring.call_forced_tool")
    def test_scores_batch_in_one_call(self, forced_tool):
        forced_tool.return_value = {
            "scores": [_score(candidate.id) for candidate in self.candidates]
        }

        result = _score_candidates(self.candidates)

        self.assertEqual([item.id for item in result], ["first-id", "second-id"])
        self.assertTrue(all(item.bourdain_score == 4 for item in result))
        self.assertTrue(all(not item.passed_guardrail for item in result))
        self.assertEqual(forced_tool.call_count, 1)
        self.assertEqual(forced_tool.call_args.kwargs["model"], SCORING_MODEL)
        scores_schema = forced_tool.call_args.kwargs["tool_schema"]["input_schema"][
            "properties"
        ]["scores"]
        self.assertEqual(scores_schema["minItems"], 2)
        self.assertEqual(scores_schema["maxItems"], 2)

    @patch("app.graph.scoring.call_forced_tool")
    def test_matches_reordered_scores_by_candidate_id(self, forced_tool):
        forced_tool.return_value = {
            "scores": [
                _score("second-id", score=2),
                _score("first-id", score=5),
            ]
        }

        result = _score_candidates(self.candidates)

        self.assertEqual([item.id for item in result], ["first-id", "second-id"])
        self.assertEqual([item.bourdain_score for item in result], [5, 2])

    @patch("app.graph.scoring.call_forced_tool", side_effect=RuntimeError("api down"))
    def test_repeated_failure_retries_then_raises(self, forced_tool):
        with self.assertRaisesRegex(ScoringError, "after one retry"):
            _score_candidates(self.candidates)

        self.assertEqual(forced_tool.call_count, 2)

    @patch("app.graph.scoring.call_forced_tool")
    def test_unknown_candidate_id_retries_then_raises(self, forced_tool):
        forced_tool.return_value = {
            "scores": [_score("first-id"), _score("unknown-id")]
        }

        with self.assertRaisesRegex(ScoringError, "after one retry") as raised:
            _score_candidates(self.candidates)

        self.assertEqual(forced_tool.call_count, 2)
        self.assertIn("unknown candidate_id", str(raised.exception.__cause__))


class ScoringNodeTests(IsolatedAsyncioTestCase):
    @patch("app.graph.scoring._score_candidates")
    async def test_scores_category_with_one_batch(self, score_candidates):
        candidates = [_candidate("one"), _candidate("two")]
        score_candidates.return_value = []

        result = await scoring_node({"graded_candidates": candidates})

        self.assertEqual(result["scored_recommendations"], [])
        score_candidates.assert_called_once_with(candidates)
