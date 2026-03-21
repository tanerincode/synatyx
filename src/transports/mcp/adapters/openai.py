from __future__ import annotations

import json
from typing import Any

from src.transports.mcp.server import TOOL_DEFINITIONS


def to_openai_tools() -> list[dict[str, Any]]:
    """
    Transform internal tool definitions to OpenAI tool format.

    OpenAI format:
    {
        "type": "function",
        "function": {
            "name": "tool_name",
            "description": "...",
            "parameters": {
                "type": "object",
                "properties": { ... },
                "required": [ ... ]
            }
        }
    }
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in TOOL_DEFINITIONS
    ]


def parse_openai_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Parse an OpenAI tool_call object into (tool_name, arguments).

    OpenAI tool_call:
    {
        "id": "call_xxx",
        "type": "function",
        "function": {
            "name": "context_retrieve",
            "arguments": "{\"query\": \"...\"}"
        }
    }
    """
    name = tool_call["function"]["name"]
    arguments = json.loads(tool_call["function"]["arguments"])
    return name, arguments


def to_openai_tool_result(tool_call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """
    Wrap a tool result into OpenAI tool message format.

    {
        "role": "tool",
        "tool_call_id": "call_xxx",
        "content": "<json string>"
    }
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result, default=str),
    }

