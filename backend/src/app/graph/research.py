"""Per-category retrieval, CRAG grading, and web fallback node."""

import asyncio
import json
import logging
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter

from app.db.places import get_or_create_place
from app.db.recommendations import create_recommendation
from app.db.research_runs import (
    complete_research_run,
    create_evidence,
    create_research_run,
)
from app.graph.guardrails import guardrail_node
from app.graph.scoring import scoring_node
from app.graph.web_fallback_agent import run_web_fallback_agent
from app.models.schemas import (
    Candidate,
    Category,
    GradedCandidate,
    ScoredRecommendation,
)
from app.services.embeddings import create_embeddings
from app.services.llm import call_forced_tool
from app.services.places import resolve_city, verify_venue
from app.services.vector_store import (
    get_shared_pool,
    insert_candidate,
    query_nearest_neighbors,
)
from app.services.web_search import WebSearchResult


logger = logging.getLogger(__name__)

RESEARCH_MODEL = "claude-haiku-4-5"


class ResearchCategoryError(RuntimeError):
    """Raised when a category cannot complete research and grading."""


class _Grade(BaseModel):
    candidate_id: int
    relevance_score: float
    authenticity_signal: str
    confidence: str
    needs_fallback: bool


class _ExtractedVenue(BaseModel):
    name: str
    description: str
    source_url: str


_GRADE_LIST_ADAPTER = TypeAdapter(list[_Grade])
_EXTRACTED_VENUE_LIST_ADAPTER = TypeAdapter(list[_ExtractedVenue])


def _grading_tool_schema(candidate_count: int) -> dict[str, Any]:
    return {
        "name": "grade_research_candidates",
        "description": "Grades every supplied candidate individually.",
        "input_schema": {
            "type": "object",
            "properties": {
                "grades": {
                    "type": "array",
                    "minItems": candidate_count,
                    "maxItems": candidate_count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "candidate_id": {"type": "integer"},
                            "relevance_score": {"type": "number", "minimum": 0, "maximum": 1},
                            "authenticity_signal": {"type": "string"},
                            "confidence": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                            },
                            "needs_fallback": {"type": "boolean"},
                        },
                        "required": [
                            "candidate_id",
                            "relevance_score",
                            "authenticity_signal",
                            "confidence",
                            "needs_fallback",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["grades"],
            "additionalProperties": False,
        },
    }


def _venue_extraction_tool_schema() -> dict[str, Any]:
    return {
        "name": "extract_venues",
        "description": "Extracts specific named venues from web search results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "venues": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "source_url": {"type": "string"},
                        },
                        "required": ["name", "description", "source_url"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["venues"],
            "additionalProperties": False,
        },
    }


def _extract_venues(
    category: Category, web_results: list[WebSearchResult]
) -> list[_ExtractedVenue]:
    payload = [
        {"title": result.title, "url": result.url, "content": result.content}
        for result in web_results
    ]
    tool_input = call_forced_tool(
        system_prompt=(
            "Extract every specific, named venue or place mentioned across the "
            "supplied web results. Skip generic mentions such as neighborhoods, "
            "the food scene, or a category in general when they are not specific "
            "places. Merge duplicate mentions of the same venue across results "
            "into one entry. Keep each venue's description to one or two sentences "
            "of specific, authentic detail; this is a candidate summary, not a full "
            "write-up. Return at most 5 venues. If more than 5 are mentioned, "
            "prioritize the most specific, well-evidenced venues across the supplied "
            "results. For each venue, choose the single most relevant source_url from "
            "only the URLs present in the supplied results. Use the tool once."
        ),
        user_prompt=(
            f"Category: {category.name}\nRationale: {category.rationale}\n\n"
            f"Web results:\n{json.dumps(payload, ensure_ascii=False)}"
        ),
        tool_schema=_venue_extraction_tool_schema(),
        model=RESEARCH_MODEL,
        max_tokens=8000,
    )
    return _EXTRACTED_VENUE_LIST_ADAPTER.validate_python(tool_input["venues"])


def _grade_candidates(
    category: Category, candidates: list[Candidate]
) -> list[GradedCandidate]:
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
                system_prompt=(
                    "You are the CRAG grader for The Bourdain Brief. Assess every "
                    "candidate independently for relevance and authentic, specific, "
                    "locally grounded signal; do not merely rank candidates against "
                    "one another. Candidates are presented in order 1 through N. Echo "
                    "each candidate's integer candidate_id index exactly; do not echo "
                    "or copy any id field that appears elsewhere in the payload. Mark "
                    "needs_fallback when that candidate lacks sufficient credible "
                    "evidence and broader web research is warranted. Use the tool once."
                ),
                user_prompt=(
                    f"Category: {category.name}\nRationale: {category.rationale}\n\n"
                    f"Candidates:\n{json.dumps(payload, ensure_ascii=False)}"
                ),
                tool_schema=_grading_tool_schema(len(candidates)),
                model=RESEARCH_MODEL,
                max_tokens=4000,
            )
            grades = _GRADE_LIST_ADAPTER.validate_python(tool_input["grades"])
            candidates_by_id = {candidate.id: candidate for candidate in candidates}
            if len(candidates_by_id) != len(candidates):
                raise ValueError("candidate batch contains duplicate candidate ids")

            grades_by_id: dict[int, _Grade] = {}
            for grade in grades:
                if grade.candidate_id not in candidates_by_index:
                    raise ValueError(
                        "grader returned unknown candidate_id "
                        f"'{grade.candidate_id}'"
                    )
                if grade.candidate_id in grades_by_id:
                    raise ValueError(
                        "grader returned duplicate grade for candidate_id "
                        f"'{grade.candidate_id}'"
                    )
                grades_by_id[grade.candidate_id] = grade

            missing_ids = candidates_by_index.keys() - grades_by_id.keys()
            if missing_ids:
                raise ValueError(
                    "grader omitted grades for candidate_id(s): "
                    f"{', '.join(str(index) for index in sorted(missing_ids))}"
                )

            return [
                GradedCandidate(
                    **candidate.model_dump(),
                    **grades_by_id[index].model_dump(exclude={"candidate_id"}),
                )
                for index, candidate in candidates_by_index.items()
            ]
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                logger.warning(
                    "research_grader_retry_attempt",
                    extra={
                        "category": category.name,
                        "candidate_count": len(candidates),
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )

    raise ResearchCategoryError(
        f"CRAG grading failed for category '{category.name}' after one retry."
    ) from last_error


async def _verify_candidates(
    category: Category,
    candidates: list[Candidate],
    *,
    city_name: str,
    location_bias: tuple[float, float],
) -> list[Candidate]:
    """Attach Places identity data and discard candidates that cannot be verified."""

    neighborhood_scope = category.neighborhood_scope or category.name
    verification_results = await asyncio.gather(
        *(
            verify_venue(
                candidate.name,
                neighborhood_scope=neighborhood_scope,
                city_name=city_name,
                location_bias=location_bias,
            )
            for candidate in candidates
        ),
        return_exceptions=True,
    )
    verified: list[Candidate] = []
    for candidate, match in zip(candidates, verification_results, strict=True):
        if isinstance(match, BaseException) or match is None:
            continue
        place = await get_or_create_place(
            google_place_id=match.google_place_id,
            name=match.name,
            formatted_address=match.formatted_address,
            lat=match.lat,
            lng=match.lng,
            google_types=match.google_types,
        )
        verified.append(
            candidate.model_copy(
                update={
                    "internal_place_id": place.id,
                    "place_id": match.google_place_id,
                    "lat": match.lat,
                    "lng": match.lng,
                    "formatted_address": match.formatted_address,
                    "google_types": match.google_types,
                }
            )
        )
    return verified


async def _resolve_location_bias(city_name: str) -> tuple[float, float]:
    resolution = await resolve_city(city_name)
    if resolution.status != "resolved" or resolution.match is None:
        raise ResearchCategoryError(
            f"Destination coordinates could not be resolved for {city_name!r}."
        )
    return resolution.match.lat, resolution.match.lng


async def _persist_final_recommendations(
    recommendations: list[ScoredRecommendation],
    *,
    trip_id: UUID,
    category: Category,
    research_run_id: UUID,
) -> None:
    assert category.id is not None
    for recommendation in recommendations:
        if recommendation.internal_place_id is None:
            logger.warning(
                "research_recommendation_persistence_skipped",
                extra={
                    "category": category.name,
                    "recommendation_id": recommendation.id,
                    "reason": "missing_internal_place_id",
                },
            )
            continue

        await create_evidence(
            place_id=recommendation.internal_place_id,
            research_run_id=research_run_id,
            source_type=recommendation.source,
            raw_content=recommendation.description,
        )
        places_content = json.dumps(
            {
                "formatted_address": recommendation.formatted_address,
                "google_types": recommendation.google_types,
                "name": recommendation.name,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        await create_evidence(
            place_id=recommendation.internal_place_id,
            research_run_id=research_run_id,
            source_type="places_api",
            raw_content=places_content,
        )
        await create_recommendation(
            trip_id=trip_id,
            category_id=category.id,
            research_run_id=research_run_id,
            place_id=recommendation.internal_place_id,
            relevance_score=recommendation.relevance_score,
            authenticity_signal=recommendation.authenticity_signal,
            confidence=recommendation.confidence,
            needs_fallback=recommendation.needs_fallback,
            bourdain_score=recommendation.bourdain_score,
            scoring_rationale=recommendation.scoring_rationale,
            locally_owned_signal=recommendation.locally_owned_signal,
            passed_guardrail=recommendation.passed_guardrail,
            guardrail_note=recommendation.guardrail_note,
        )


async def research_category(
    category: Category,
    city_slug: str,
    city_name: str,
    trip_id: UUID,
    trigger_reason: Literal["initial", "supervisor_replan"],
    location_bias: tuple[float, float] | None = None,
) -> dict[str, list[ScoredRecommendation]]:
    """Retrieve and grade candidates for one Send API category invocation."""

    if category.id is None:
        raise ResearchCategoryError(
            f"Category '{category.name}' must be persisted before research."
        )

    initial_run = await create_research_run(
        trip_id=trip_id,
        category_id=category.id,
        trigger_reason=trigger_reason,
    )
    owning_run_id = initial_run.id

    logger.info(
        "research_category_start",
        extra={"category": category.name, "candidate_count": 0},
    )
    query = f"{category.name}: {category.rationale}"
    try:
        query_embedding = (await asyncio.to_thread(create_embeddings, [query]))[0]
    except Exception as exc:
        raise ResearchCategoryError(
            f"Embedding failed for category '{category.name}'."
        ) from exc

    try:
        pool = await get_shared_pool()
        results = await query_nearest_neighbors(
            pool,
            query_embedding=query_embedding,
            city_slug=city_slug,
            top_k=5,
        )
    except Exception as exc:
        raise ResearchCategoryError(
            f"Vector retrieval failed for category '{category.name}'."
        ) from exc

    candidates = [
        Candidate(
            id=str(result.id),
            name=result.name,
            category=category.name,
            description=result.content,
            source="vector_store",
            source_url=(
                result.metadata.get("source_url", result.metadata.get("url"))
                if isinstance(
                    result.metadata.get("source_url", result.metadata.get("url")),
                    str,
                )
                else None
            ),
            raw_signal=result.content,
        )
        for result in results
    ]
    logger.info(
        "research_retrieval_complete",
        extra={"category": category.name, "candidate_count": len(candidates)},
    )

    if candidates:
        if location_bias is None:
            location_bias = await _resolve_location_bias(city_name)
        candidates = await _verify_candidates(
            category,
            candidates,
            city_name=city_name,
            location_bias=location_bias,
        )
        logger.info(
            "research_verification_complete",
            extra={"category": category.name, "candidate_count": len(candidates)},
        )

    if candidates:
        graded = await asyncio.to_thread(_grade_candidates, category, candidates)
        logger.info(
            "research_grading_complete",
            extra={
                "category": category.name,
                "candidate_count": len(graded),
                "fallback_count": sum(item.needs_fallback for item in graded),
            },
        )
    else:
        graded = []
        logger.info(
            "research_grading_skipped",
            extra={
                "category": category.name,
                "reason": "no_verified_vector_candidates",
            },
        )

    await complete_research_run(initial_run.id)

    fallback_triggered = not candidates or any(
        item.needs_fallback for item in graded
    )
    logger.info(
        "research_fallback_decision",
        extra={
            "category": category.name,
            "candidate_count": len(graded),
            "fallback_triggered": fallback_triggered,
        },
    )
    if fallback_triggered:
        fallback_run = await create_research_run(
            trip_id=trip_id,
            category_id=category.id,
            trigger_reason="crag_fallback",
        )
        try:
            web_results = await run_web_fallback_agent(category, city_name)
        except Exception as exc:
            raise ResearchCategoryError(
                f"Web fallback failed for category '{category.name}'."
            ) from exc
        extracted_venues = await asyncio.to_thread(
            _extract_venues, category, web_results
        )
        web_candidates = [
            Candidate(
                id=str(uuid4()),
                name=venue.name,
                category=category.name,
                description=venue.description,
                source="web_search",
                source_url=venue.source_url,
                raw_signal=venue.description,
            )
            for venue in extracted_venues
        ]
        if web_candidates:
            if location_bias is None:
                location_bias = await _resolve_location_bias(city_name)
            web_candidates = await _verify_candidates(
                category,
                web_candidates,
                city_name=city_name,
                location_bias=location_bias,
            )
        candidates.extend(web_candidates)
        graded = (
            await asyncio.to_thread(_grade_candidates, category, candidates)
            if candidates
            else []
        )
        logger.info(
            "research_grading_complete",
            extra={
                "category": category.name,
                "candidate_count": len(graded),
                "fallback_count": sum(item.needs_fallback for item in graded),
                "post_fallback": True,
            },
        )
        await complete_research_run(fallback_run.id)
        owning_run_id = fallback_run.id

    logger.info(
        "research_category_complete",
        extra={
            "category": category.name,
            "candidate_count": len(graded),
            "fallback_triggered": fallback_triggered,
        },
    )

    scoring_result = await scoring_node(  # type: ignore[arg-type]
        {"graded_candidates": graded}
    )
    guardrail_result = await asyncio.to_thread(  # type: ignore[arg-type]
        guardrail_node, scoring_result
    )
    all_graded_recommendations = guardrail_result["scored_recommendations"]
    scored_recommendations = sorted(
        (
            recommendation
            for recommendation in all_graded_recommendations
            if recommendation.passed_guardrail
        ),
        key=lambda recommendation: (
            recommendation.bourdain_score,
            recommendation.relevance_score,
        ),
        reverse=True,
    )

    await _persist_final_recommendations(
        all_graded_recommendations,
        trip_id=trip_id,
        category=category,
        research_run_id=owning_run_id,
    )

    web_recommendations = [
        recommendation
        for recommendation in scored_recommendations
        if recommendation.source == "web_search" and recommendation.passed_guardrail
    ]
    try:
        if web_recommendations:
            embeddings = await asyncio.to_thread(
                create_embeddings,
                [recommendation.description for recommendation in web_recommendations],
            )
            await asyncio.gather(
                *(
                    insert_candidate(
                        pool,
                        name=recommendation.name,
                        content=recommendation.description,
                        category=category.name,
                        city_slug=city_slug,
                        embedding=embedding,
                        metadata={"source_url": recommendation.source_url},
                        candidate_id=UUID(recommendation.id),
                    )
                    for recommendation, embedding in zip(
                        web_recommendations, embeddings, strict=True
                    )
                )
            )
    except Exception:
        logger.exception(
            "research_vector_store_write_failed",
            extra={
                "city_slug": city_slug,
                "category": category.name,
                "candidate_count": len(web_recommendations),
            },
        )

    return {"scored_recommendations": scored_recommendations}
