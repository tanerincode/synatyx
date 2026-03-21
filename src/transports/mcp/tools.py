from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_TOOLS_PATH = Path(__file__).parent / "tools.json"


@lru_cache(maxsize=1)
def load_tool_definitions() -> list[dict[str, Any]]:
    """Load MCP tool definitions from tools.json. Cached after first load."""
    return json.loads(_TOOLS_PATH.read_text(encoding="utf-8"))


TOOL_DEFINITIONS: list[dict[str, Any]] = load_tool_definitions()

