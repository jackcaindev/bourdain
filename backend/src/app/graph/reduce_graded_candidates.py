"""Deterministic reduction of accumulated research candidates."""

import logging

from app.graph.state import BriefState
from app.models.schemas import GradedCandidate


logger = logging.getLogger(__name__)


def reduce_graded_candidates(
    state: BriefState,
) -> dict[str, list[GradedCandidate]]:
    """Expose only graded candidates belonging to the current categories."""

    category_names = {category.name for category in state["categories"]}
    retained = [
        candidate
        for candidate in state["candidates"]
        if candidate.category in category_names
    ]

    for candidate in retained:
        if not isinstance(candidate, GradedCandidate):
            raise TypeError(
                "reduce_graded_candidates expected every retained candidate to be "
                f"a GradedCandidate; candidate '{candidate.id}' is "
                f"{type(candidate).__name__}."
            )

    unique_candidates: list[GradedCandidate] = []
    seen_ids: set[str] = set()
    for candidate in retained:
        if candidate.id in seen_ids:
            continue
        # A multi-category candidate keeps its first label: a deliberate v1 simplification.
        seen_ids.add(candidate.id)
        unique_candidates.append(candidate)

    duplicate_count = len(retained) - len(unique_candidates)
    logger.info(
        "reduce_graded_candidates_complete",
        extra={
            "candidate_count_before_dedup": len(retained),
            "duplicate_count": duplicate_count,
            "candidate_count_after_dedup": len(unique_candidates),
        },
    )

    return {"graded_candidates": unique_candidates}
