from unittest import TestCase

from app.services.web_search import WebSearchError, WebSearchResult, search_web


class _Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class SearchWebTests(TestCase):
    def test_normalizes_tavily_results(self):
        client = _Client(
            {
                "results": [
                    {
                        "title": "Market",
                        "url": "https://x",
                        "content": "Local stalls",
                    }
                ]
            }
        )

        results = search_web("night markets", client=client)

        self.assertEqual(
            results,
            [WebSearchResult(title="Market", url="https://x", content="Local stalls")],
        )
        self.assertEqual(client.calls[0]["query"], "night markets")

    def test_wraps_client_failures(self):
        with self.assertRaises(WebSearchError) as raised:
            search_web("night markets", client=_Client(error=OSError("secret detail")))

        self.assertEqual(str(raised.exception), "Tavily web search failed.")
        self.assertIsInstance(raised.exception.__cause__, OSError)
