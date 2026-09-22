from __future__ import annotations

import json
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.core.scoped_keys import (
    AdminScope,
    authorize_request,
    parse_scoped_keys,
    resolve_scope,
)
from src.transports.mcp.http_server import AdminKeyAuthMiddleware

ADMIN_KEY = "owner-secret"
CX_KEY = "cx-tenant-secret"

CX_DEFINITION = [
    {
        "name": "cx",
        "key": CX_KEY,
        "projects": ["cx-"],
        "tools": ["context_ingest", "context_retrieve"],
        "routes": ["/mcp", "/v1/ingest", "/v1/retrieve", "/v1/health"],
    }
]


def cx_key():
    return parse_scoped_keys(json.dumps(CX_DEFINITION))[0]


def tool_call(tool: str, **arguments: Any) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    ).encode()


# ---------------------------------------------------------------------------
# Parsing: an allowlist that is empty grants nothing
# ---------------------------------------------------------------------------

def test_an_empty_allowlist_grants_nothing_rather_than_everything():
    # The failure this design exists to prevent: a typo that widens a key.
    key = parse_scoped_keys('[{"name": "oops", "key": "k"}]')[0]

    assert key.allows_tool("context_retrieve") is False
    assert key.allows_route("/v1/ingest") is False
    assert key.allows_project("cx-a") is False


def test_a_key_without_a_secret_is_dropped():
    assert parse_scoped_keys('[{"name": "no-key", "projects": ["cx-"]}]') == []


def test_a_malformed_document_grants_nothing():
    assert parse_scoped_keys("{not json") == []
    assert parse_scoped_keys('{"name": "not-a-list"}') == []


def test_one_bad_entry_does_not_cost_the_others_their_access():
    keys = parse_scoped_keys(json.dumps(["nonsense", CX_DEFINITION[0]]))

    assert [k.name for k in keys] == ["cx"]


def test_an_unknown_key_resolves_to_no_scope():
    assert resolve_scope(b"guessed", ADMIN_KEY, [cx_key()]) is None
    assert resolve_scope(None, ADMIN_KEY, [cx_key()]) is None


def test_the_admin_key_still_resolves_to_full_access():
    assert isinstance(resolve_scope(ADMIN_KEY.encode(), ADMIN_KEY, [cx_key()]), AdminScope)


# ---------------------------------------------------------------------------
# The boundary itself
# ---------------------------------------------------------------------------

def test_a_tenant_may_reach_its_own_projects():
    allowed = authorize_request(
        cx_key(), "/mcp", tool_call("context_retrieve", project="cx-lens-ai")
    )

    assert allowed is None


def test_a_tenant_may_not_reach_another_tenants_project():
    denial = authorize_request(
        cx_key(), "/mcp", tool_call("context_retrieve", project="taty-v2")
    )

    assert denial is not None and "taty-v2" in denial


def test_an_unspecified_project_is_refused_rather_than_defaulted():
    # Without a project the call lands in the server's default collection,
    # which belongs to whoever set the server up — not to this tenant.
    denial = authorize_request(cx_key(), "/mcp", tool_call("context_retrieve"))

    assert denial is not None


def test_session_id_cannot_be_used_to_smuggle_a_project():
    # session_id doubles as the project slug across the tool surface, so a
    # check that only read `project` would be trivially bypassable.
    denial = authorize_request(
        cx_key(), "/mcp", tool_call("context_retrieve", session_id="taty-v2")
    )

    assert denial is not None and "taty-v2" in denial


def test_a_tool_outside_the_allowlist_is_refused():
    denial = authorize_request(
        cx_key(), "/mcp", tool_call("context_erase_user", project="cx-a")
    )

    assert denial is not None and "context_erase_user" in denial


def test_reading_resources_is_refused_because_they_serve_the_owners_memory():
    denial = authorize_request(
        cx_key(), "/mcp", json.dumps({"method": "resources/read"}).encode()
    )

    assert denial is not None


def test_handshake_methods_are_allowed():
    for method in ("initialize", "tools/list", "ping"):
        assert authorize_request(cx_key(), "/mcp", json.dumps({"method": method}).encode()) is None


def test_every_message_in_a_batch_is_checked():
    batch = json.dumps([
        {"method": "tools/call",
         "params": {"name": "context_retrieve", "arguments": {"project": "cx-a"}}},
        {"method": "tools/call",
         "params": {"name": "context_retrieve", "arguments": {"project": "taty-v2"}}},
    ]).encode()

    # The allowed call first, so a check that stopped at the first entry would pass.
    assert authorize_request(cx_key(), "/mcp", batch) is not None


def test_an_unreadable_body_is_refused_not_waved_through():
    assert authorize_request(cx_key(), "/mcp", b"\xff\xfe not json") is not None


def test_an_unknown_path_is_refused_even_under_an_allowed_prefix():
    key = parse_scoped_keys(json.dumps([{**CX_DEFINITION[0], "routes": ["/v1/"]}]))[0]

    # A route prefix must not quietly grant routes invented later.
    assert authorize_request(key, "/v1/something-new", b"{}") is not None


def test_a_route_prefix_does_not_grant_a_capability_the_tool_list_withholds():
    # "/v1/" covers /v1/users/... too. The tool allowlist is what stops it.
    key = parse_scoped_keys(json.dumps([{**CX_DEFINITION[0], "routes": ["/v1/"]}]))[0]

    denial = authorize_request(key, "/v1/users/someone", None)

    assert denial is not None and "context_erase_user" in denial


def test_health_needs_no_project():
    assert authorize_request(cx_key(), "/v1/health", None) is None


def test_the_admin_scope_is_unrestricted():
    assert authorize_request(AdminScope(), "/dashboard/api/items", None) is None
    assert authorize_request(
        AdminScope(), "/mcp", tool_call("context_erase_user", project="any")
    ) is None


# ---------------------------------------------------------------------------
# Through the real middleware, over HTTP
# ---------------------------------------------------------------------------

async def echo(request: Request) -> JSONResponse:
    """Proves the body survived being read for the authorization check."""
    return JSONResponse({"seen": (await request.body()).decode() or None})


def app() -> TestClient:
    routes = [Route("/mcp", echo, methods=["POST"]), Route("/v1/ingest", echo, methods=["POST"])]
    application = Starlette(routes=routes)
    application.add_middleware(
        AdminKeyAuthMiddleware,
        admin_key=ADMIN_KEY,
        header_name="X-Auth-Key",
        public_paths=frozenset({"/health"}),
        scoped_keys=[cx_key()],
    )
    return TestClient(application)


def test_no_key_is_a_401():
    response = app().post("/mcp", content=tool_call("context_retrieve", project="cx-a"))

    assert response.status_code == 401


def test_a_scoped_key_on_its_own_project_passes_and_the_body_arrives_intact():
    body = tool_call("context_retrieve", project="cx-a")
    response = app().post("/mcp", content=body, headers={"X-Auth-Key": CX_KEY})

    assert response.status_code == 200
    # The middleware consumed the body to inspect it; the app must still see it.
    assert response.json()["seen"] == body.decode()


def test_a_scoped_key_on_someone_elses_project_is_a_403():
    response = app().post(
        "/mcp", content=tool_call("context_retrieve", project="taty-v2"),
        headers={"X-Auth-Key": CX_KEY},
    )

    assert response.status_code == 403
    assert "taty-v2" in response.json()["detail"]


def test_a_scoped_key_calling_a_withheld_tool_is_a_403():
    response = app().post(
        "/mcp", content=tool_call("context_erase_user", project="cx-a"),
        headers={"X-Auth-Key": CX_KEY},
    )

    assert response.status_code == 403


def test_the_admin_key_is_not_subjected_to_scope_checks():
    response = app().post(
        "/mcp", content=tool_call("context_erase_user", project="anything"),
        headers={"X-Auth-Key": ADMIN_KEY},
    )

    assert response.status_code == 200


def test_a_bearer_token_carrying_the_admin_key_still_works():
    response = app().post(
        "/v1/ingest", content=b'{"project": "cx-a"}',
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )

    assert response.status_code == 200
