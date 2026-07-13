"""Concurrent per-candidate scoring for The Bourdain Brief."""

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.graph.state import BriefState
from app.models.schemas import GradedCandidate, ScoredRecommendation
from app.services.llm import call_forced_tool


logger = logging.getLogger(__name__)

SCORING_MODEL = "claude-sonnet-4-6"


class ScoringError(RuntimeError):
    """Raised when one candidate cannot be scored after a retry."""


class _CandidateScore(BaseModel):
    bourdain_score: int = Field(ge=1, le=5)
    scoring_rationale: str
    locally_owned_signal: str | None = None


def _scoring_tool_schema() -> dict[str, Any]:
    return {
        "name": "score_bourdain_candidate",
        "description": "Applies the Bourdain rubric to one graded candidate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bourdain_score": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                },
                "scoring_rationale": {"type": "string"},
                "locally_owned_signal": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": (
                        "Ownership evidence quoted or closely paraphrased only from "
                        "raw_signal or authenticity_signal; null when absent."
                    ),
                },
            },
            "required": [
                "bourdain_score",
                "scoring_rationale",
                "locally_owned_signal",
            ],
            "additionalProperties": False,
        },
    }


def _system_prompt() -> str:
    return """You score one graded candidate at a time for The Bourdain Brief. Apply this exact rubric:

1 — Corporate chain or franchise; success driven by marketing, foot traffic, or brand recognition rather than local roots. No independent character.
2 — Independently operated but generic — nothing distinctive, could be swapped for any similar business in any city. Or: touristy in character/marketing even if not literally a chain.
3 — Genuinely local, some real character, but unremarkable — a solid, honest local business without a distinguishing story, history, or cultural weight.
4 — Clearly locally-owned with real community reputation and some cultural or historical weight, though not necessarily widely known even locally.
5 — Deeply authentic and independently owned, with genuine historical or cultural significance to this specific place — the kind of spot a local would point a visitor to specifically because it reveals something true about the place, not because it's famous or heavily reviewed. Popularity is irrelevant to this score in either direction.

Judge only from the supplied candidate evidence. Populate locally_owned_signal only when raw_signal or authenticity_signal actually contains ownership evidence; otherwise return null. Never infer or invent ownership evidence. Use the provided tool exactly once."""


def _user_prompt(candidate: GradedCandidate) -> str:
    return (
        "Score this single graded candidate independently. Do not compare it with "
        "other candidates.\n\n"
        f"Candidate:\n{json.dumps(candidate.model_dump(mode='json'), ensure_ascii=False)}"
    )


def _score_candidate(candidate: GradedCandidate) -> ScoredRecommendation:
    """Score one candidate, retrying once on API or validation failure."""

    last_error: Exception | None = None

    for attempt in range(2):
        try:
            tool_input = call_forced_tool(
                system_prompt=_system_prompt(),
                user_prompt=_user_prompt(candidate),
                tool_schema=_scoring_tool_schema(),
                model=SCORING_MODEL,
                max_tokens=1000,
            )
            score = _CandidateScore.model_validate(tool_input)
            return ScoredRecommendation(
                **candidate.model_dump(),
                **score.model_dump(),
                passed_guardrail=False,
                guardrail_note=None,
            )
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                logger.warning(
                    "scoring_candidate_retry_attempt",
                    extra={
                        "candidate_id": candidate.id,
                        "candidate_name": candidate.name,
                        "category": candidate.category,
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )

    raise ScoringError(
        f"Scoring failed for candidate '{candidate.id}' ({candidate.name}) "
        f"after one retry: {last_error}"
    ) from last_error


async def scoring_node(
    state: BriefState,
) -> dict[str, list[ScoredRecommendation]]:
    """Score all graded candidates concurrently and drop exhausted failures."""

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

    results = await asyncio.gather(
        *(asyncio.to_thread(_score_candidate, candidate) for candidate in candidates),
        return_exceptions=True,
    )

    scored_recommendations: list[ScoredRecommendation] = []
    for candidate, result in zip(candidates, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "scoring_candidate_dropped",
                extra={
                    "candidate_id": candidate.id,
                    "candidate_name": candidate.name,
                    "category": candidate.category,
                    "error": str(result),
                    "error_type": type(result).__name__,
                },
            )
            continue
        scored_recommendations.append(result)

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
