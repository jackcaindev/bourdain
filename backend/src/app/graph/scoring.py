"""Per-category batch scoring for The Bourdain Brief."""

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter

from app.graph.state import BriefState
from app.models.schemas import GradedCandidate, ScoredRecommendation
from app.services.llm import call_forced_tool


logger = logging.getLogger(__name__)

SCORING_MODEL = "claude-haiku-4-5"


class ScoringError(RuntimeError):
    """Raised when a candidate batch cannot be scored after a retry."""


class _CandidateScore(BaseModel):
    candidate_id: int
    bourdain_score: int = Field(ge=1, le=5)
    scoring_rationale: str
    locally_owned_signal: str | None = None


_CANDIDATE_SCORE_LIST_ADAPTER = TypeAdapter(list[_CandidateScore])


def _scoring_tool_schema(candidate_count: int) -> dict[str, Any]:
    return {
        "name": "score_bourdain_candidates",
        "description": "Applies the Bourdain rubric to every graded candidate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scores": {
                    "type": "array",
                    "minItems": candidate_count,
                    "maxItems": candidate_count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "candidate_id": {"type": "integer"},
                            "bourdain_score": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                            },
                            "scoring_rationale": {"type": "string"},
                            "locally_owned_signal": {
                                "anyOf": [{"type": "string"}, {"type": "null"}],
                                "description": (
                                    "Ownership evidence quoted or closely paraphrased "
                                    "only from raw_signal or authenticity_signal; null "
                                    "when absent."
                                ),
                            },
                        },
                        "required": [
                            "candidate_id",
                            "bourdain_score",
                            "scoring_rationale",
                            "locally_owned_signal",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["scores"],
            "additionalProperties": False,
        },
    }


def _system_prompt() -> str:
    return """You score a batch of graded candidates for The Bourdain Brief. Score every candidate independently rather than ranking or comparing candidates. Candidates are presented in order 1 through N. Echo each candidate's integer candidate_id index exactly; do not echo or copy any id field that appears elsewhere in the payload. Apply this exact rubric:

1 — Corporate chain or franchise; success driven by marketing, foot traffic, or brand recognition rather than local roots. No independent character.
2 — Independently operated but generic — nothing distinctive, could be swapped for any similar business in any city. Or: touristy in character/marketing even if not literally a chain.
3 — Genuinely local, some real character, but unremarkable — a solid, honest local business without a distinguishing story, history, or cultural weight.
4 — Clearly locally-owned with real community reputation and some cultural or historical weight, though not necessarily widely known even locally.
5 — Deeply authentic and independently owned, with genuine historical or cultural significance to this specific place — the kind of spot a local would point a visitor to specifically because it reveals something true about the place, not because it's famous or heavily reviewed. Popularity is irrelevant to this score in either direction.

Judge only from each supplied candidate's evidence. Populate locally_owned_signal only when raw_signal or authenticity_signal actually contains ownership evidence; otherwise return null. Never infer or invent ownership evidence. Use the provided tool exactly once."""


def _user_prompt(candidates: list[dict[str, Any]]) -> str:
    return (
        "Score every graded candidate independently. Do not compare candidates "
        "with one another.\n\n"
        f"Candidates:\n{json.dumps(candidates, ensure_ascii=False)}"
    )


def _score_candidates(
    candidates: list[GradedCandidate],
) -> list[ScoredRecommendation]:
    """Score one category's candidates, retrying once on API or validation failure."""

    if not candidates:
        return []

    candidates_by_index = {
        index: candidate for index, candidate in enumerate(candidates, start=1)
    }
    payload = [
        {**candidate.model_dump(mode="json"), "candidate_id": index}
        for index, candidate in candidates_by_index.items()
    ]
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            tool_input = call_forced_tool(
                system_prompt=_system_prompt(),
                user_prompt=_user_prompt(payload),
                tool_schema=_scoring_tool_schema(len(candidates)),
                model=SCORING_MODEL,
                max_tokens=4000,
            )
            scores = _CANDIDATE_SCORE_LIST_ADAPTER.validate_python(
                tool_input["scores"]
            )
            candidates_by_id = {candidate.id: candidate for candidate in candidates}
            if len(candidates_by_id) != len(candidates):
                raise ValueError("candidate batch contains duplicate candidate ids")

            scores_by_id: dict[int, _CandidateScore] = {}
            for score in scores:
                if score.candidate_id not in candidates_by_index:
                    raise ValueError(
                        "scorer returned unknown candidate_id "
                        f"'{score.candidate_id}'"
                    )
                if score.candidate_id in scores_by_id:
                    raise ValueError(
                        "scorer returned duplicate score for candidate_id "
                        f"'{score.candidate_id}'"
                    )
                scores_by_id[score.candidate_id] = score

            missing_ids = candidates_by_index.keys() - scores_by_id.keys()
            if missing_ids:
                raise ValueError(
                    "scorer omitted scores for candidate_id(s): "
                    f"{', '.join(str(index) for index in sorted(missing_ids))}"
                )

            return [
                ScoredRecommendation(
                    **candidate.model_dump(),
                    **scores_by_id[index].model_dump(
                        exclude={"candidate_id"}
                    ),
                    passed_guardrail=False,
                    guardrail_note=None,
                )
                for index, candidate in candidates_by_index.items()
            ]
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                logger.warning(
                    "scoring_batch_retry_attempt",
                    extra={
                        "category": candidates[0].category,
                        "candidate_count": len(candidates),
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )

    raise ScoringError(
        f"Scoring failed for category '{candidates[0].category}' after one retry."
    ) from last_error


async def scoring_node(
    state: BriefState,
) -> dict[str, list[ScoredRecommendation]]:
    """Score all graded candidates in one category-level batch."""

    candidates = state["graded_candidates"]
    total_attempted = len(candidates)
    logger.info(
        "scoring_node_start",
        extra={
            "candidate_count": total_attempted,
            "total_attempted": total_attempted,
            "total_scored": 0,
            "total_dropped": 0,
        },
    )

    # Batched scoring reduces one LLM call per candidate to one per category, but
    # the batch now succeeds or fails as a unit. After its retry, let ScoringError
    # reach research_category's category-level error boundary instead of retaining
    # partial per-candidate results.
    scored_recommendations = await asyncio.to_thread(
        _score_candidates, candidates
    )

    total_scored = len(scored_recommendations)
    total_dropped = total_attempted - total_scored
    logger.info(
        "scoring_node_complete",
        extra={
            "candidate_count": total_scored,
            "total_scored": total_scored,
            "total_attempted": total_attempted,
            "total_dropped": total_dropped,
        },
    )

    return {"scored_recommendations": scored_recommendations}
