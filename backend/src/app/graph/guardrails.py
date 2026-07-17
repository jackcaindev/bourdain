"""Two-stage grounding guardrails for scored recommendations."""

import json
import logging
from typing import Any

from pydantic import BaseModel, TypeAdapter, model_validator

from app.graph.state import BriefState
from app.models.schemas import ScoredRecommendation
from app.services.llm import call_forced_tool


logger = logging.getLogger(__name__)

GUARDRAIL_MODEL = "claude-haiku-4-5"
MIN_RAW_SIGNAL_LENGTH = 20


class GuardrailCheckError(RuntimeError):
    """Raised when the batched grounding check cannot complete after a retry."""


class _GroundingResult(BaseModel):
    recommendation_id: str
    is_grounded: bool
    guardrail_note: str | None

    @model_validator(mode="after")
    def validate_note(self) -> "_GroundingResult":
        if self.is_grounded and self.guardrail_note is not None:
            raise ValueError("grounded recommendations must have a null guardrail_note")
        if not self.is_grounded and not (self.guardrail_note or "").strip():
            raise ValueError("ungrounded recommendations must explain the unsupported claim")
        return self


_GROUNDING_RESULTS_ADAPTER = TypeAdapter(list[_GroundingResult])


def _grounding_tool_schema(recommendation_count: int) -> dict[str, Any]:
    return {
        "name": "check_recommendation_grounding",
        "description": "Checks whether every scoring rationale is grounded in its evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "minItems": recommendation_count,
                    "maxItems": recommendation_count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "recommendation_id": {"type": "string"},
                            "is_grounded": {"type": "boolean"},
                            "guardrail_note": {
                                "anyOf": [{"type": "string"}, {"type": "null"}],
                                "description": (
                                    "Null when grounded; otherwise a concise explanation "
                                    "of the specific unsupported claim."
                                ),
                            },
                        },
                        "required": [
                            "recommendation_id",
                            "is_grounded",
                            "guardrail_note",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    }


def _check_grounding(
    recommendations: list[ScoredRecommendation],
) -> dict[str, _GroundingResult]:
    """Check one batch, retrying once on API, schema, or ID validation failure."""

    payload = [
        {
            "id": recommendation.id,
            "raw_signal": recommendation.raw_signal,
            "authenticity_signal": recommendation.authenticity_signal,
            "scoring_rationale": recommendation.scoring_rationale,
        }
        for recommendation in recommendations
    ]
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            tool_input = call_forced_tool(
                system_prompt=(
                    "You are the grounding guardrail for The Bourdain Brief. Check "
                    "whether each scoring_rationale follows honestly from that "
                    "recommendation's raw_signal and authenticity_signal. Flag claims "
                    "of specific history, ownership, cultural significance, or other "
                    "facts that the supplied evidence does not support. This is not a "
                    "re-scoring task: never second-guess or change the bourdain_score. "
                    "Judge every supplied recommendation, echo each id exactly in "
                    "recommendation_id, and use the tool once for the whole batch. For "
                    "a grounded rationale return a null guardrail_note; for an "
                    "ungrounded rationale identify the unsupported claim concisely."
                ),
                user_prompt=(
                    "Check this batch of scored recommendations for rationale "
                    f"grounding:\n{json.dumps(payload, ensure_ascii=False)}"
                ),
                tool_schema=_grounding_tool_schema(len(recommendations)),
                model=GUARDRAIL_MODEL,
                max_tokens=2000,
            )
            results = _GROUNDING_RESULTS_ADAPTER.validate_python(tool_input["results"])
            recommendations_by_id = {
                recommendation.id: recommendation for recommendation in recommendations
            }
            if len(recommendations_by_id) != len(recommendations):
                raise ValueError("guardrail batch contains duplicate recommendation ids")

            results_by_id: dict[str, _GroundingResult] = {}
            for result in results:
                if result.recommendation_id not in recommendations_by_id:
                    raise ValueError(
                        "guardrail returned unknown recommendation_id "
                        f"'{result.recommendation_id}'"
                    )
                if result.recommendation_id in results_by_id:
                    raise ValueError(
                        "guardrail returned duplicate result for recommendation_id "
                        f"'{result.recommendation_id}'"
                    )
                results_by_id[result.recommendation_id] = result

            missing_ids = recommendations_by_id.keys() - results_by_id.keys()
            if missing_ids:
                raise ValueError(
                    "guardrail omitted results for recommendation_id(s): "
                    f"{', '.join(sorted(missing_ids))}"
                )

            return results_by_id
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                logger.warning(
                    "guardrail_stage_2_retry_attempt",
                    extra={
                        "recommendation_count": len(recommendations),
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )

    raise GuardrailCheckError(
        "Stage 2 grounding check failed after one retry."
    ) from last_error


def guardrail_node(
    state: BriefState,
) -> dict[str, list[ScoredRecommendation]]:
    """Run deterministic and batched LLM checks without dropping recommendations."""

    recommendations = state["scored_recommendations"]
    logger.info(
        "guardrail_node_start",
        extra={"recommendation_count": len(recommendations)},
    )

    stage_2_candidates: list[ScoredRecommendation] = []
    deterministic_notes: list[str | None] = []
    for recommendation in recommendations:
        evidence_length = len(recommendation.raw_signal.strip())
        if evidence_length < MIN_RAW_SIGNAL_LENGTH:
            deterministic_notes.append(
                f"insufficient raw_signal evidence: {evidence_length} characters"
            )
        else:
            deterministic_notes.append(None)
            stage_2_candidates.append(recommendation)

    deterministic_fail_count = sum(note is not None for note in deterministic_notes)
    logger.info(
        "guardrail_stage_1_complete",
        extra={
            "recommendation_count": len(recommendations),
            "deterministic_fail_count": deterministic_fail_count,
            "stage_2_candidate_count": len(stage_2_candidates),
        },
    )

    grounding_results: dict[str, _GroundingResult] = {}
    check_incomplete = False
    if stage_2_candidates:
        try:
            grounding_results = _check_grounding(stage_2_candidates)
        except GuardrailCheckError as exc:
            check_incomplete = True
            logger.warning(
                "guardrail_stage_2_incomplete",
                extra={
                    "recommendation_count": len(stage_2_candidates),
                    "error": str(exc),
                    "error_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                },
            )

    llm_fail_count = sum(
        not result.is_grounded for result in grounding_results.values()
    )
    check_incomplete_count = len(stage_2_candidates) if check_incomplete else 0
    logger.info(
        "guardrail_stage_2_complete",
        extra={
            "recommendation_count": len(stage_2_candidates),
            "passed_count": len(grounding_results) - llm_fail_count,
            "llm_fail_count": llm_fail_count,
            "check_incomplete_count": check_incomplete_count,
        },
    )

    checked_recommendations: list[ScoredRecommendation] = []
    for recommendation, deterministic_note in zip(
        recommendations, deterministic_notes, strict=True
    ):
        if deterministic_note is not None:
            passed = False
            note = deterministic_note
        elif check_incomplete:
            passed = False
            note = "grounding guardrail check could not complete after one retry"
        else:
            result = grounding_results[recommendation.id]
            passed = result.is_grounded
            note = result.guardrail_note

        checked_recommendations.append(
            recommendation.model_copy(
                update={"passed_guardrail": passed, "guardrail_note": note}
            )
        )

    passed_count = sum(item.passed_guardrail for item in checked_recommendations)
    logger.info(
        "guardrail_node_complete",
        extra={
            "recommendation_count": len(checked_recommendations),
            "passed_count": passed_count,
            "flagged_count": len(checked_recommendations) - passed_count,
            "deterministic_fail_count": deterministic_fail_count,
            "llm_fail_count": llm_fail_count,
            "check_incomplete_count": check_incomplete_count,
        },
    )

    return {"scored_recommendations": checked_recommendations}
