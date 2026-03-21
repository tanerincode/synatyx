from __future__ import annotations

from typing import Any

from src.transports.mcp.tools import TOOL_DEFINITIONS


def to_anthropic_tools() -> list[dict[str, Any]]:
    """
    Transform internal tool definitions to Anthropic tool format.

    Anthropic format:
    {
        "name": "tool_name",
        "description": "...",
        "input_schema": {
            "type": "object",
            "properties": { ... },
            "required": [ ... ]
        }
    }
    """
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["parameters"],
        }
        for tool in TOOL_DEFINITIONS
    ]


def parse_anthropic_tool_use(tool_use_block: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Parse an Anthropic tool_use content block into (tool_name, arguments).

    Anthropic tool_use block:
    {
        "type": "tool_use",
        "id": "toolu_xxx",
        "name": "context_retrieve",
        "input": { ... }
    }
    """
    return tool_use_block["name"], tool_use_block["input"]


def to_anthropic_tool_result(tool_use_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """
    Wrap a tool result into Anthropic tool_result format.

    {
        "type": "tool_result",
        "tool_use_id": "toolu_xxx",
        "content": "<json string>"
    }
    """
    import json
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(result, default=str),
    }

