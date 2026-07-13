"""Agentic web-search fallback for under-supported research categories."""

import logging
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.models.schemas import Category
from app.services.web_search import WebSearchResult, search_web_tool


logger = logging.getLogger(__name__)

WEB_FALLBACK_MODEL = "claude-sonnet-4-6"
MAX_SEARCH_TOOL_CALLS = 3

_SYSTEM_PROMPT = (
    "You research under-supported categories for The Bourdain Brief. Choose your "
    "own web searches and refine them as needed. Seek sufficient credible evidence "
    "with authentic, specific, locally grounded, non-generic detail about this "
    "category for this destination. Judge for yourself when the evidence is "
    "sufficient, then stop searching."
)


class WebFallbackAgentError(RuntimeError):
    """Raised when the web fallback agent cannot complete its run."""


def _create_fallback_agent(
    *, model: Any = WEB_FALLBACK_MODEL, search_tool: BaseTool = search_web_tool
) -> Any:
    return create_agent(
        model=model,
        tools=[search_tool],
        system_prompt=_SYSTEM_PROMPT,
        middleware=[
            ToolCallLimitMiddleware(
                tool_name=search_tool.name,
                run_limit=MAX_SEARCH_TOOL_CALLS,
                exit_behavior="end",
            )
        ],
    )


def _collect_results(messages: list[Any]) -> tuple[list[WebSearchResult], bool]:
    queries_by_call_id: dict[str, str] = {}
    results: list[WebSearchResult] = []
    hit_cap = False
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                query = call.get("args", {}).get("query")
                if isinstance(query, str):
                    queries_by_call_id[call["id"]] = query
        elif isinstance(message, ToolMessage):
            if message.status == "error":
                hit_cap = "call limit exceeded" in str(message.content).lower()
                continue
            query = queries_by_call_id.get(message.tool_call_id)
            logger.info(
                "web_fallback_agent_tool_call",
                extra={"query": query},
            )
            if isinstance(message.artifact, list):
                results.extend(
                    result
                    for result in message.artifact
                    if isinstance(result, WebSearchResult)
                )

    deduplicated = list({result.url: result for result in results}.values())
    return deduplicated, hit_cap


async def run_web_fallback_agent(category: Category) -> list[WebSearchResult]:
    """Run agent-directed searches and return every unique gathered result."""

    logger.info(
        "web_fallback_agent_start",
        extra={"category": category.name},
    )
    query = f"{category.name}: {category.rationale}"

    try:
        output = await _create_fallback_agent().ainvoke(
            {"messages": [{"role": "user", "content": query}]}
        )
        results, hit_cap = _collect_results(output["messages"])
    except Exception as exc:
        raise WebFallbackAgentError(
            f"Web fallback agent failed for category '{category.name}'."
        ) from exc

    logger.info(
        "web_fallback_agent_complete",
        extra={
            "category": category.name,
            "stop_reason": "tool_call_cap" if hit_cap else "agent_judgment",
            "candidate_count": len(results),
        },
    )
    return results
