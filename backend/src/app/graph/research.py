"""Per-category retrieval, CRAG grading, and web fallback node."""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter

from app.graph.guardrails import guardrail_node
from app.graph.scoring import scoring_node
from app.graph.web_fallback_agent import run_web_fallback_agent
from app.models.schemas import (
    Candidate,
    Category,
    GradedCandidate,
    ScoredRecommendation,
)
from app.services.category_cache import (
    get_cached_recommendations,
    write_category_cache,
)
from app.services.embeddings import create_embeddings
from app.services.geocoding import geocode_venue
from app.services.llm import call_forced_tool
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
    candidate_id: str
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
                            "candidate_id": {"type": "string"},
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
                    "maxItems": 10,
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
            "write-up. Return at most 10 venues. If more than 10 are mentioned, "
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
    payload = [candidate.model_dump(mode="json") for candidate in candidates]
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            tool_input = call_forced_tool(
                system_prompt=(
                    "You are the CRAG grader for The Bourdain Brief. Assess every "
                    "candidate independently for relevance and authentic, specific, "
                    "locally grounded signal; do not merely rank candidates against "
                    "one another. Echo each candidate's id exactly in candidate_id so "
                    "every grade can be matched unambiguously to its candidate. Mark "
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

            grades_by_id: dict[str, _Grade] = {}
            for grade in grades:
                if grade.candidate_id not in candidates_by_id:
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

            missing_ids = candidates_by_id.keys() - grades_by_id.keys()
            if missing_ids:
                raise ValueError(
                    "grader omitted grades for candidate_id(s): "
                    f"{', '.join(sorted(missing_ids))}"
                )

            return [
                GradedCandidate(
                    **candidate.model_dump(),
                    **grades_by_id[candidate.id].model_dump(exclude={"candidate_id"}),
                )
                for candidate in candidates
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


async def research_category(
    category: Category, city_slug: str, city_name: str
) -> dict[str, list[ScoredRecommendation]]:
    """Retrieve and grade candidates for one Send API category invocation."""

    cached_recommendations = await get_cached_recommendations(
        city_slug, category.name
    )
    if cached_recommendations is not None:
        logger.debug(
            "research_category_cache_hit",
            extra={
                "city_slug": city_slug,
                "category": category.name,
                "candidate_count": len(cached_recommendations),
            },
        )
        return {
            "scored_recommendations": [
                recommendation.model_copy(update={"source": "cache"})
                for recommendation in cached_recommendations
            ]
        }

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
                "reason": "no_vector_candidates",
            },
        )

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
        try:
            web_results = await run_web_fallback_agent(category, city_name)
        except Exception as exc:
            raise ResearchCategoryError(
                f"Web fallback failed for category '{category.name}'."
            ) from exc
        extracted_venues = await asyncio.to_thread(
            _extract_venues, category, web_results
        )
        candidates.extend(
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
        )
        graded = await asyncio.to_thread(_grade_candidates, category, candidates)
        logger.info(
            "research_grading_complete",
            extra={
                "category": category.name,
                "candidate_count": len(graded),
                "fallback_count": sum(item.needs_fallback for item in graded),
                "post_fallback": True,
            },
        )

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
    scored_recommendations = guardrail_result["scored_recommendations"]

    geocoding_results = await asyncio.gather(
        *(
            geocode_venue(recommendation.name, city_name)
            for recommendation in scored_recommendations
        ),
        return_exceptions=True,
    )
    for recommendation, coordinates in zip(
        scored_recommendations, geocoding_results, strict=True
    ):
        if isinstance(coordinates, BaseException) or coordinates is None:
            continue
        recommendation.lat, recommendation.lng = coordinates

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

    try:
        await write_category_cache(
            city_slug, category.name, scored_recommendations
        )
    except Exception:
        logger.exception(
            "research_category_cache_write_failed",
            extra={
                "city_slug": city_slug,
                "category": category.name,
                "candidate_count": len(scored_recommendations),
            },
        )

    return {"scored_recommendations": scored_recommendations}
