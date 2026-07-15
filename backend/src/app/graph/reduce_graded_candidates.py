"""Deterministic reduction of accumulated scored recommendations."""

import logging

from langgraph.types import Overwrite

from app.graph.state import BriefState
from app.models.schemas import ScoredRecommendation


logger = logging.getLogger(__name__)


def reduce_scored_recommendations(
    state: BriefState,
) -> dict[str, Overwrite]:
    """Deduplicate scored recommendations belonging to current categories."""

    category_names = {category.name for category in state["categories"]}
    retained = [
        recommendation
        for recommendation in state["scored_recommendations"]
        if recommendation.category in category_names
    ]

    unique_recommendations: list[ScoredRecommendation] = []
    seen_ids: set[str] = set()
    for recommendation in retained:
        if recommendation.id in seen_ids:
            continue
        # A multi-category recommendation keeps its first label in v1.
        seen_ids.add(recommendation.id)
        unique_recommendations.append(recommendation)

    duplicate_count = len(retained) - len(unique_recommendations)
    logger.info(
        "reduce_scored_recommendations_complete",
        extra={
            "recommendation_count_before_dedup": len(retained),
            "duplicate_count": duplicate_count,
            "recommendation_count_after_dedup": len(unique_recommendations),
        },
    )

    return {
        "scored_recommendations": Overwrite(value=unique_recommendations)
    }
