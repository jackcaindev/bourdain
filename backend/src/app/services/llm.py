"""Thin Anthropic SDK wrapper for reusable forced-tool calls."""

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    Anthropic,
    RateLimitError,
)

from app.config import get_settings


class LLMToolUseError(RuntimeError):
    """Raised when a forced tool call does not produce usable tool input."""


class LLMTruncatedResponseError(LLMToolUseError):
    """Raised when a forced tool response reaches its token limit."""


_TRANSIENT_ERRORS = (RateLimitError, APIConnectionError, APITimeoutError)
_MAX_RETRIES = 3
_INITIAL_RETRY_DELAY = 1.0
_BACKOFF_FACTOR = 2.0
_MAX_RETRY_DELAY = 30.0
_JITTER_FRACTION = 0.25


def _retry_after_delay(error: RateLimitError) -> float | None:
    """Return a bounded Retry-After delay when the response supplies one."""

    retry_after = error.response.headers.get("retry-after")
    if retry_after is None:
        return None

    try:
        delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None

    return min(max(delay, 0.0), _MAX_RETRY_DELAY)


def _retry_delay(error: Exception, retry_number: int) -> float:
    if isinstance(error, RateLimitError):
        retry_after = _retry_after_delay(error)
        if retry_after is not None:
            return retry_after

    base_delay = min(
        _INITIAL_RETRY_DELAY * (_BACKOFF_FACTOR**retry_number),
        _MAX_RETRY_DELAY,
    )
    jitter = random.uniform(  # noqa: S311
        -base_delay * _JITTER_FRACTION,
        base_delay * _JITTER_FRACTION,
    )
    return min(max(base_delay + jitter, 0.0), _MAX_RETRY_DELAY)


@lru_cache
def _create_anthropic_client() -> Anthropic:
    settings = get_settings()
    return Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.anthropic_timeout_seconds,
    )


def call_forced_tool(
    *,
    system_prompt: str,
    user_prompt: str,
    tool_schema: dict[str, Any],
    model: str,
    max_tokens: int = 1024,
    client: Anthropic | None = None,
) -> dict[str, Any]:
    """Force Claude to call one tool and return the parsed tool input."""

    tool_name = tool_schema.get("name")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("tool_schema must include a non-empty string 'name'")

    anthropic_client = client or _create_anthropic_client()
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with anthropic_client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": tool_name},
            ) as stream:
                response = stream.get_final_message()
            break
        except _TRANSIENT_ERRORS as exc:
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(_retry_delay(exc, attempt))

    if response.stop_reason == "max_tokens":
        raise LLMTruncatedResponseError(
            "Forced tool response was truncated; increase max_tokens for this call."
        )

    for content_block in response.content:
        block_type = getattr(content_block, "type", None)
        block_name = getattr(content_block, "name", None)
        block_input = getattr(content_block, "input", None)

        if isinstance(content_block, dict):
            block_type = content_block.get("type")
            block_name = content_block.get("name")
            block_input = content_block.get("input")

        if block_type == "tool_use" and block_name == tool_name:
            if not isinstance(block_input, dict):
                raise LLMToolUseError(
                    f"Forced tool '{tool_name}' returned non-object input."
                )
            return block_input

    raise LLMToolUseError(f"Forced tool '{tool_name}' was not returned by the model.")
