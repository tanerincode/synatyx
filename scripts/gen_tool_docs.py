#!/usr/bin/env python3
"""Generate docs/mcp-tools.md from src/transports/mcp/tools.json.

tools.json is the single source of truth for tool names, descriptions, and
parameters — this script keeps the reference doc from drifting (it used to be
a hand-maintained mirror). Run after any tools.json change:

    python scripts/gen_tool_docs.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_JSON = ROOT / "src" / "transports" / "mcp" / "tools.json"
OUT = ROOT / "docs" / "mcp-tools.md"

# Display grouping — every tool must appear in exactly one category.
CATEGORIES: dict[str, list[str]] = {
    "Context Assembly": ["context_brief", "context_pack"],
    "Project Management": ["context_set_project", "context_get_project"],
    "Memory": [
        "context_store", "context_retrieve", "context_summarize", "context_score",
    ],
    "Code & Doc Index": [
        "context_index", "context_index_search", "context_index_status",
    ],
    "Knowledge": [
        "context_checkpoint", "context_deprecate", "context_list", "context_ingest",
    ],
    "Relations & Graph": [
        "context_relate", "context_unrelate", "context_related",
        "context_get", "context_visualize", "context_alternatives",
    ],
    "Tasks": ["context_task_add", "context_task_list", "context_task_update"],
    "Skills": [
        "context_skill_store", "context_skill_find", "context_skill_get",
        "context_skill_list", "context_skill_delete",
    ],
    "Maintenance": ["context_consolidate", "context_gc_stats", "context_usage"],
}


def render_tool(tool: dict) -> str:
    lines = [f"### `{tool['name']}`", "", tool["description"], ""]
    props = tool["parameters"].get("properties", {})
    required = set(tool["parameters"].get("required", []))
    if props:
        lines.append("| Param | Type | Required | Description |")
        lines.append("|-------|------|----------|-------------|")
        for name, spec in props.items():
            ptype = spec.get("type", "any")
            if "enum" in spec:
                ptype = " \\| ".join(f"`{v}`" for v in spec["enum"])
            req = "yes" if name in required else "no"
            desc = spec.get("description", "").replace("\n", " ")
            lines.append(f"| `{name}` | {ptype} | {req} | {desc} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    tools = {t["name"]: t for t in json.loads(TOOLS_JSON.read_text())}

    categorized = [name for names in CATEGORIES.values() for name in names]
    missing = [n for n in tools if n not in categorized]
    orphaned = [n for n in categorized if n not in tools]
    if missing:
        raise SystemExit(f"Uncategorized tools (add to CATEGORIES): {missing}")
    if orphaned:
        raise SystemExit(f"CATEGORIES references unknown tools: {orphaned}")

    out = [
        "# MCP Tools Reference",
        "",
        f"Synatyx exposes **{len(tools)} MCP tools**. This file is generated "
        "from `src/transports/mcp/tools.json` by `scripts/gen_tool_docs.py` — "
        "edit the JSON, then regenerate.",
        "",
    ]
    for category, names in CATEGORIES.items():
        out.append(f"## {category}")
        out.append("")
        for name in names:
            out.append(render_tool(tools[name]))
    OUT.write_text("\n".join(out).rstrip() + "\n")
    print(f"Wrote {OUT} ({len(tools)} tools)")


if __name__ == "__main__":
    main()
