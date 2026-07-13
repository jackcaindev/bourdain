from unittest import IsolatedAsyncioTestCase

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from app.graph.web_fallback_agent import (
    MAX_SEARCH_TOOL_CALLS,
    _collect_results,
    _create_fallback_agent,
)
from app.services.web_search import WebSearchResult


class _AlwaysSearchModel(BaseChatModel):
    call_count: int = 0

    @property
    def _llm_type(self):
        return "always-search"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_count += 1
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "counted_search",
                    "args": {"query": f"query-{self.call_count}"},
                    "id": f"call-{self.call_count}",
                    "type": "tool_call",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class WebFallbackAgentCapTests(IsolatedAsyncioTestCase):
    async def test_executes_exactly_three_searches_and_blocks_fourth(self):
        executed_queries = []

        @tool(response_format="content_and_artifact")
        def counted_search(query: str):
            """Search for evidence."""
            executed_queries.append(query)
            result = WebSearchResult(query, f"https://example.com/{query}", query)
            return query, [result]

        agent = _create_fallback_agent(
            model=_AlwaysSearchModel(), search_tool=counted_search
        )
        output = await agent.ainvoke({"messages": [("user", "research this")]})
        results, hit_cap = _collect_results(output["messages"])

        self.assertEqual(len(executed_queries), MAX_SEARCH_TOOL_CALLS)
        self.assertEqual(executed_queries, ["query-1", "query-2", "query-3"])
        self.assertTrue(hit_cap)
        self.assertEqual(len(results), MAX_SEARCH_TOOL_CALLS)
