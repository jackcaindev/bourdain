"""Thin Anthropic SDK wrapper for reusable forced-tool calls."""

from typing import Any

from anthropic import Anthropic

from app.config import get_settings


class LLMToolUseError(RuntimeError):
    """Raised when a forced tool call does not produce usable tool input."""


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
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[tool_schema],
        tool_choice={"type": "tool", "name": tool_name},
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
