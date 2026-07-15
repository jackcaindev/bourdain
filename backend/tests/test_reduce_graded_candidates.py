from unittest import TestCase

from app.graph.reduce_graded_candidates import reduce_scored_recommendations
from app.models.schemas import Category, ScoredRecommendation


def _recommendation(candidate_id: str, category: str) -> ScoredRecommendation:
    return ScoredRecommendation(
        id=candidate_id,
        name=candidate_id,
        category=category,
        description="Description",
        source="vector_store",
        raw_signal="Specific local evidence",
        relevance_score=0.8,
        authenticity_signal="Locally grounded",
        confidence="high",
        needs_fallback=False,
        bourdain_score=4,
        scoring_rationale="Strong local fit",
        locally_owned_signal=None,
        passed_guardrail=True,
        guardrail_note=None,
    )


class ReduceScoredRecommendationsTests(TestCase):
    def test_filters_stale_categories_and_preserves_order(self):
        first = _recommendation("first", "Food")
        stale = _recommendation("stale", "Nightlife")
        second = _recommendation("second", "Markets")

        result = reduce_scored_recommendations(
            {
                "categories": [
                    Category(name="Food", rationale="Food rationale"),
                    Category(name="Markets", rationale="Market rationale"),
                ],
                "scored_recommendations": [first, stale, second],
            }
        )

        self.assertEqual(
            [item.id for item in result["scored_recommendations"].value],
            ["first", "second"],
        )

    def test_returns_empty_list_when_nothing_matches(self):
        result = reduce_scored_recommendations(
            {
                "categories": [Category(name="Food", rationale="Food rationale")],
                "scored_recommendations": [
                    _recommendation("stale", "Nightlife")
                ],
            }
        )

        self.assertEqual(result["scored_recommendations"].value, [])

    def test_deduplicates_by_id_and_keeps_first_category_label(self):
        first = _recommendation("shared", "Food")
        duplicate = _recommendation("shared", "Markets")
        distinct = _recommendation("distinct", "Markets")

        result = reduce_scored_recommendations(
            {
                "categories": [
                    Category(name="Food", rationale="Food rationale"),
                    Category(name="Markets", rationale="Market rationale"),
                ],
                "scored_recommendations": [first, duplicate, distinct],
            }
        )

        self.assertEqual(
            [
                (item.id, item.category)
                for item in result["scored_recommendations"].value
            ],
            [("shared", "Food"), ("distinct", "Markets")],
        )

    def test_logs_deduplication_counts(self):
        with self.assertLogs(
            "app.graph.reduce_graded_candidates", level="INFO"
        ) as logs:
            reduce_scored_recommendations(
                {
                    "categories": [
                        Category(name="Food", rationale="Food rationale")
                    ],
                    "scored_recommendations": [
                        _recommendation("shared", "Food"),
                        _recommendation("shared", "Food"),
                    ],
                }
            )

        record = logs.records[0]
        self.assertEqual(record.recommendation_count_before_dedup, 2)
        self.assertEqual(record.duplicate_count, 1)
        self.assertEqual(record.recommendation_count_after_dedup, 1)
