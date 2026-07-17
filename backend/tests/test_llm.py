from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import httpx
from anthropic import APIConnectionError, APITimeoutError, RateLimitError

from app.services.llm import call_forced_tool


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _rate_limit_error(*, retry_after: str | None = None) -> RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else None
    response = httpx.Response(429, headers=headers, request=_request())
    return RateLimitError("rate limited", response=response, body=None)


def _successful_stream() -> MagicMock:
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            {
                "type": "tool_use",
                "name": "test_tool",
                "input": {"answer": "ok"},
            }
        ],
    )
    context_manager = MagicMock()
    context_manager.__enter__.return_value.get_final_message.return_value = response
    return context_manager


def _call(client: MagicMock) -> dict[str, object]:
    return call_forced_tool(
        system_prompt="system",
        user_prompt="user",
        tool_schema={"name": "test_tool", "input_schema": {"type": "object"}},
        model="test-model",
        client=client,
    )


class CallForcedToolRetryTests(TestCase):
    @patch("app.services.llm.time.sleep")
    @patch("app.services.llm.random.uniform", return_value=0.0)
    def test_retries_transient_failures_with_exponential_backoff(
        self, _jitter, sleep
    ):
        client = MagicMock()
        client.messages.stream.side_effect = [
            _rate_limit_error(),
            _rate_limit_error(),
            _successful_stream(),
        ]

        result = _call(client)

        self.assertEqual(result, {"answer": "ok"})
        self.assertEqual(client.messages.stream.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1.0), call(2.0)])

    @patch("app.services.llm.time.sleep")
    @patch("app.services.llm.random.uniform", return_value=0.0)
    def test_retries_connection_and_timeout_errors(self, _jitter, sleep):
        transient_errors = [
            APIConnectionError(request=_request()),
            APITimeoutError(_request()),
        ]

        for error in transient_errors:
            with self.subTest(error_type=type(error).__name__):
                client = MagicMock()
                client.messages.stream.side_effect = [error, _successful_stream()]

                self.assertEqual(_call(client), {"answer": "ok"})
                self.assertEqual(client.messages.stream.call_count, 2)

        self.assertEqual(sleep.call_args_list, [call(1.0), call(1.0)])

    @patch("app.services.llm.time.sleep")
    def test_honors_retry_after_header(self, sleep):
        client = MagicMock()
        client.messages.stream.side_effect = [
            _rate_limit_error(retry_after="12.5"),
            _successful_stream(),
        ]

        self.assertEqual(_call(client), {"answer": "ok"})

        sleep.assert_called_once_with(12.5)

    @patch("app.services.llm.time.sleep")
    @patch("app.services.llm.random.uniform", return_value=0.0)
    def test_propagates_transient_error_after_three_retries(self, _jitter, sleep):
        client = MagicMock()
        error = APIConnectionError(request=_request())
        client.messages.stream.side_effect = error

        with self.assertRaises(APIConnectionError):
            _call(client)

        self.assertEqual(client.messages.stream.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(1.0), call(2.0), call(4.0)])

    @patch("app.services.llm.time.sleep")
    def test_does_not_retry_non_transient_error(self, sleep):
        client = MagicMock()
        client.messages.stream.side_effect = ValueError("invalid request")

        with self.assertRaisesRegex(ValueError, "invalid request"):
            _call(client)

        client.messages.stream.assert_called_once()
        sleep.assert_not_called()
