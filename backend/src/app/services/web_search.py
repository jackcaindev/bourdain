"""Tavily web-search boundary used by research fallback."""

from dataclasses import dataclass
import json
from typing import Any

from langchain.tools import tool
from tavily import TavilyClient

from app.config import get_settings


class WebSearchError(RuntimeError):
    """Raised when Tavily cannot return a usable search response."""


@dataclass(frozen=True)
class WebSearchResult:
    """Normalized Tavily result consumed by graph nodes."""

    title: str
    url: str
    content: str


def search_web(
    query: str, *, client: TavilyClient | None = None
) -> list[WebSearchResult]:
    """Search Tavily once and return normalized result fields."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    try:
        tavily_client = client or TavilyClient(
            api_key=get_settings().tavily_api_key.get_secret_value()
        )
        response: dict[str, Any] = tavily_client.search(
            query=query,
            max_results=5,
            search_depth="advanced",
        )
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            raise TypeError("Tavily response did not contain a results list")

        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                raise TypeError("Tavily returned a malformed result")
            title = item.get("title")
            url = item.get("url")
            content = item.get("content")
            if not all(isinstance(value, str) for value in (title, url, content)):
                raise TypeError("Tavily result fields were malformed")
            results.append(WebSearchResult(title=title, url=url, content=content))
        return results
    except WebSearchError:
        raise
    except Exception as exc:
        raise WebSearchError("Tavily web search failed.") from exc


@tool(response_format="content_and_artifact")
def search_web_tool(query: str) -> tuple[str, list[WebSearchResult]]:
    """Search the web for locally grounded evidence about a destination category."""

    results = search_web(query)
    content = json.dumps(
        [
            {"title": result.title, "url": result.url, "content": result.content}
            for result in results
        ],
        ensure_ascii=False,
    )
    return content, results
