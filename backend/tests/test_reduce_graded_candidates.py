from unittest import TestCase

from app.graph.reduce_graded_candidates import reduce_graded_candidates
from app.models.schemas import Candidate, Category, GradedCandidate


def _candidate(
    candidate_id: str, category: str, *, graded: bool = True
) -> Candidate:
    values = {
        "id": candidate_id,
        "name": candidate_id,
        "category": category,
        "description": "Description",
        "source": "vector_store",
        "raw_signal": "Specific local evidence",
    }
    if not graded:
        return Candidate(**values)
    return GradedCandidate(
        **values,
        relevance_score=0.8,
        authenticity_signal="Locally grounded",
        confidence="high",
        needs_fallback=False,
    )


class ReduceGradedCandidatesTests(TestCase):
    def test_filters_stale_categories_and_preserves_order(self):
        first = _candidate("first", "Food")
        stale = _candidate("stale", "Nightlife")
        second = _candidate("second", "Markets")

        result = reduce_graded_candidates(
            {
                "categories": [
                    Category(name="Food", rationale="Food rationale"),
                    Category(name="Markets", rationale="Market rationale"),
                ],
                "candidates": [first, stale, second],
            }
        )

        self.assertEqual(
            [item.id for item in result["graded_candidates"]],
            ["first", "second"],
        )

    def test_returns_empty_list_when_nothing_matches(self):
        result = reduce_graded_candidates(
            {
                "categories": [Category(name="Food", rationale="Food rationale")],
                "candidates": [_candidate("stale", "Nightlife")],
            }
        )

        self.assertEqual(result, {"graded_candidates": []})

    def test_deduplicates_by_id_and_keeps_first_category_label(self):
        first = _candidate("shared", "Food")
        duplicate = _candidate("shared", "Markets")
        distinct = _candidate("distinct", "Markets")

        result = reduce_graded_candidates(
            {
                "categories": [
                    Category(name="Food", rationale="Food rationale"),
                    Category(name="Markets", rationale="Market rationale"),
                ],
                "candidates": [first, duplicate, distinct],
            }
        )

        self.assertEqual(
            [(item.id, item.category) for item in result["graded_candidates"]],
            [("shared", "Food"), ("distinct", "Markets")],
        )

    def test_logs_deduplication_counts(self):
        with self.assertLogs(
            "app.graph.reduce_graded_candidates", level="INFO"
        ) as logs:
            reduce_graded_candidates(
                {
                    "categories": [
                        Category(name="Food", rationale="Food rationale")
                    ],
                    "candidates": [
                        _candidate("shared", "Food"),
                        _candidate("shared", "Food"),
                    ],
                }
            )

        record = logs.records[0]
        self.assertEqual(record.candidate_count_before_dedup, 2)
        self.assertEqual(record.duplicate_count, 1)
        self.assertEqual(record.candidate_count_after_dedup, 1)

    def test_rejects_retained_bare_candidate(self):
        with self.assertRaisesRegex(TypeError, "GradedCandidate.*bare"):
            reduce_graded_candidates(
                {
                    "categories": [
                        Category(name="Food", rationale="Food rationale")
                    ],
                    "candidates": [_candidate("bare", "Food", graded=False)],
                }
            )

    def test_validates_duplicate_before_deduplicating(self):
        with self.assertRaisesRegex(TypeError, "GradedCandidate.*shared"):
            reduce_graded_candidates(
                {
                    "categories": [
                        Category(name="Food", rationale="Food rationale")
                    ],
                    "candidates": [
                        _candidate("shared", "Food"),
                        _candidate("shared", "Food", graded=False),
                    ],
                }
            )

    def test_does_not_validate_filtered_stale_candidate(self):
        result = reduce_graded_candidates(
            {
                "categories": [Category(name="Food", rationale="Food rationale")],
                "candidates": [_candidate("bare", "Nightlife", graded=False)],
            }
        )

        self.assertEqual(result["graded_candidates"], [])
