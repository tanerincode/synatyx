from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# JSON-RPC methods a scoped key may call without naming a tool. None of them
# reads stored content: they negotiate the connection and list what exists.
# Everything not here — resources/read and prompts/get above all, which serve
# the owner's own brief from the default user — is refused, because a key
# restricted to one tenant's projects must not have a second door into
# everyone else's memory.
_OPEN_RPC_METHODS = frozenset({
    "initialize",
    "notifications/initialized",
    "notifications/cancelled",
    "ping",
    "tools/list",
})


@dataclass(frozen=True)
class ScopedKey:
    """One API key and everything it is allowed to touch.

    Allowlists are exhaustive and empty means *nothing*, not everything. A
    scope whose empty list quietly meant "all" would turn a typo in
    configuration into a key with the run of the server, and that is precisely
    the failure this class exists to prevent.
    """

    name: str
    key: str
    project_prefixes: tuple[str, ...] = ()
    tools: frozenset[str] = frozenset()
    routes: tuple[str, ...] = ()

    @property
    def is_admin(self) -> bool:
        return False

    def allows_route(self, path: str) -> bool:
        return any(path == route or path.startswith(route) for route in self.routes)

    def allows_tool(self, tool: str) -> bool:
        return tool in self.tools

    def allows_project(self, project: str | None) -> bool:
        """A project this key may address.

        A missing project is refused rather than allowed through: without one,
        a call lands in the server's default collection, which belongs to
        whoever set it up and not to the key's tenant. "Unspecified" is the
        one case where guessing wrong leaks somebody else's memory.
        """
        if not self.project_prefixes:
            return False
        if not project:
            return False
        return any(project.startswith(prefix) for prefix in self.project_prefixes)

    def allows_rpc_method(self, method: str) -> bool:
        return method in _OPEN_RPC_METHODS


@dataclass(frozen=True)
class AdminScope:
    """The owner's key: no restrictions, and the only scope that has none."""

    name: str = "admin"

    @property
    def is_admin(self) -> bool:
        return True

    def allows_route(self, path: str) -> bool:
        return True

    def allows_tool(self, tool: str) -> bool:
        return True

    def allows_project(self, project: str | None) -> bool:
        return True

    def allows_rpc_method(self, method: str) -> bool:
        return True


Scope = ScopedKey | AdminScope


def parse_scoped_keys(raw: str) -> list[ScopedKey]:
    """Read `AUTH_SCOPED_KEYS` — a JSON array of key definitions.

    A malformed entry is dropped with a warning rather than failing startup:
    one bad entry should cost that key its access, not take the server down
    and with it every other tenant. A malformed *document*, on the other hand,
    means nobody knows what was intended, so nothing is granted.
    """
    if not raw.strip():
        return []

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("AUTH_SCOPED_KEYS is not valid JSON — no scoped keys were loaded")
        return []

    if not isinstance(entries, list):
        logger.error("AUTH_SCOPED_KEYS must be a JSON array — no scoped keys were loaded")
        return []

    keys: list[ScopedKey] = []
    for index, entry in enumerate(entries):
        parsed = _parse_entry(entry, index)
        if parsed is not None:
            keys.append(parsed)
    return keys


def _parse_entry(entry: Any, index: int) -> ScopedKey | None:
    if not isinstance(entry, dict):
        logger.error("AUTH_SCOPED_KEYS[%d] is not an object — skipped", index)
        return None

    key = str(entry.get("key") or "")
    name = str(entry.get("name") or f"scoped-{index}")
    if not key:
        logger.error("Scoped key %r has no key value — skipped", name)
        return None

    scoped = ScopedKey(
        name,
        key,
        project_prefixes=tuple(str(p) for p in entry.get("projects") or ()),
        tools=frozenset(str(t) for t in entry.get("tools") or ()),
        routes=tuple(str(r) for r in entry.get("routes") or ()),
    )

    if not scoped.tools and not scoped.routes:
        # Not a security hole — it can reach nothing — but it is certainly a
        # mistake, and silently loading it would leave someone debugging 403s
        # against a key that was never granted anything.
        logger.warning(
            "Scoped key %r allows no tools and no routes; it can do nothing", scoped.name
        )
    if not scoped.project_prefixes:
        logger.warning(
            "Scoped key %r has no project prefixes; every project-scoped call will be refused",
            scoped.name,
        )
    return scoped


def resolve_scope(
    provided: bytes | None, admin_key: str, scoped_keys: list[ScopedKey]
) -> Scope | None:
    """Which scope a presented key carries, or None when it matches nothing.

    Every candidate is compared with a constant-time comparison, and the loop
    does not stop early on a mismatch, so the time taken does not reveal how
    much of a key was right or which key it nearly was.
    """
    if provided is None:
        return None

    matched: Scope | None = None

    if admin_key and hmac.compare_digest(provided, admin_key.encode()):
        matched = AdminScope()

    for scoped in scoped_keys:
        if hmac.compare_digest(provided, scoped.key.encode()):
            matched = matched or scoped

    return matched


# Which tool each REST route ultimately runs. A key's tool allowlist therefore
# governs both transports: without this, `routes: ["/v1/"]` would hand a key
# every route under that prefix — user erasure included — while its tool list
# carefully withheld the same capability over MCP.
ROUTE_TOOLS: dict[str, str | None] = {
    "/v1/health": None,
    "/v1/ingest": "context_ingest",
    "/v1/retrieve": "context_retrieve",
    "/v1/sources/deprecate": "context_deprecate_source",
    "/v1/memory/store": "context_store",
    "/v1/memory/retrieve": "context_retrieve",
    "/v1/memory/summarize": "context_summarize",
    "/v1/users/": "context_erase_user",
}

# Routes that touch no stored content and so need no project.
_PROJECTLESS_ROUTES = frozenset({"/v1/health"})


def _tool_for_route(path: str) -> tuple[str | None, bool]:
    """(tool, known) for a REST path. An unknown path is refused, not waved through."""
    if path in ROUTE_TOOLS:
        return ROUTE_TOOLS[path], True
    for prefix, tool in ROUTE_TOOLS.items():
        if prefix.endswith("/") and path.startswith(prefix):
            return tool, True
    return None, False


def authorize_request(scope: Scope, path: str, body: bytes | None) -> str | None:
    """None when the request is allowed, otherwise why it was refused.

    The check reads the request body because that is the only place the
    interesting facts live: over MCP every call is a POST to the same path, so
    a path-only check cannot tell `context_retrieve` on one tenant's project
    from `context_erase_user` on another's.
    """
    if scope.is_admin:
        return None

    if not scope.allows_route(path):
        return f"key {scope.name!r} may not use {path}"

    if path.rstrip("/") == "/mcp":
        return _authorize_mcp(scope, body)

    if path.startswith("/v1"):
        return _authorize_rest(scope, path, body)

    # A scoped key that reaches an unrecognised path is refused: new routes
    # must be granted deliberately, not inherited by whoever holds a key.
    return f"key {scope.name!r} may not use {path}"


def _payloads(body: bytes | None) -> list[dict[str, Any]] | None:
    """Every JSON-RPC message in a body — one, or a batch. None if unreadable."""
    if not body:
        return []
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    return None


def _authorize_mcp(scope: Scope, body: bytes | None) -> str | None:
    payloads = _payloads(body)
    if payloads is None:
        # Unreadable body on an authenticated path: refuse rather than pass it
        # to a parser that might be more forgiving than this one.
        return f"key {scope.name!r} sent a body this server could not read as JSON-RPC"

    for payload in payloads:
        method = str(payload.get("method") or "")

        if method != "tools/call":
            if scope.allows_rpc_method(method):
                continue
            return f"key {scope.name!r} may not call {method or '(no method)'}"

        params = payload.get("params") or {}
        tool = str(params.get("name") or "")
        if not scope.allows_tool(tool):
            return f"key {scope.name!r} may not call tool {tool or '(unnamed)'}"

        arguments = params.get("arguments") or {}
        # session_id doubles as the project slug throughout the tool surface,
        # so a key that only checked `project` could be bypassed by sending
        # the same value under the other name.
        project = arguments.get("project") or arguments.get("session_id")
        if not scope.allows_project(project if isinstance(project, str) else None):
            return f"key {scope.name!r} may not address project {project or '(unspecified)'}"

    return None


def _authorize_rest(scope: Scope, path: str, body: bytes | None) -> str | None:
    tool, known = _tool_for_route(path)
    if not known:
        return f"key {scope.name!r} may not use {path}"

    if tool is not None and not scope.allows_tool(tool):
        return f"key {scope.name!r} may not use {path} (requires {tool})"

    if path in _PROJECTLESS_ROUTES:
        return None

    try:
        parsed = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return f"key {scope.name!r} sent a body this server could not read as JSON"

    project = parsed.get("project") if isinstance(parsed, dict) else None
    if not scope.allows_project(project if isinstance(project, str) else None):
        return f"key {scope.name!r} may not address project {project or '(unspecified)'}"

    return None
