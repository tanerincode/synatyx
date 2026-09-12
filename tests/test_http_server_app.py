"""The real `src.transports.mcp.http_server` app, wired at import time from
settings: OAuth routes and auth middleware when AUTH_ADMIN_KEY is set, and the
unchanged open server when it is empty.

The module builds its routes when imported, so each test reloads it under
monkeypatched settings and restores the original module afterwards.
"""
from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import ModuleType
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from src.config import settings
from tests.test_oauth import FakeClients, FakeKV, pkce_pair

ADMIN_KEY = "admin-key-123"
PUBLIC_URL = "http://localhost:9000"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def _reload(monkeypatch: pytest.MonkeyPatch, *, admin_key: str) -> ModuleType:
    monkeypatch.setattr(settings.auth, "admin_key", admin_key)
    monkeypatch.setattr(settings.oauth, "enabled", True)
    monkeypatch.setattr(settings, "public_url", PUBLIC_URL)
    import src.transports.mcp.http_server as http_server
    return importlib.reload(http_server)


@pytest.fixture
def restore_module() -> Iterator[None]:
    """Reload the module once more with the real settings after each test."""
    yield
    import src.transports.mcp.http_server as http_server
    importlib.reload(http_server)


def _client(module: ModuleType) -> TestClient:
    # No lifespan: storage is faked, the MCP session manager is never started.
    return TestClient(module.app, raise_server_exceptions=False)


def test_oauth_enabled_app_serves_discovery_and_protects_everything_else(
    monkeypatch: pytest.MonkeyPatch, restore_module: None
) -> None:
    module = _reload(monkeypatch, admin_key=ADMIN_KEY)
    assert module._oauth_provider is not None
    module._oauth_provider.bind(clients=FakeClients(), kv=FakeKV())
    client = _client(module)

    metadata = client.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    assert metadata.json()["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200

    # public: the login page answers (400 without a rid) instead of 401
    assert client.get("/oauth/login").status_code == 400
    assert client.get("/health").status_code == 200

    # protected: /mcp and the REST routes challenge without a credential
    for path in ("/mcp", "/capture", "/dashboard/api/overview"):
        response = client.get(path) if path != "/capture" else client.post(path, json={})
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == (
            f'Bearer resource_metadata="{PUBLIC_URL}/.well-known/oauth-protected-resource"'
        )

    # the admin key passes the middleware (503 = handler reached, storage not wired)
    assert client.post("/capture", json={}, headers={"X-Auth-Key": ADMIN_KEY}).status_code == 503

    # and a token from the real handshake passes too
    verifier, challenge = pkce_pair()
    registered = client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "client_name": "claude.ai",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    assert registered.status_code == 201, registered.text
    body = registered.json()
    assert body["client_secret_expires_at"] is not None
    authorize = client.get(
        "/authorize",
        params={
            "client_id": body["client_id"],
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "s",
        },
        follow_redirects=False,
    )
    rid = parse_qs(urlparse(authorize.headers["location"]).query)["rid"][0]
    page = client.get("/oauth/login", params={"rid": rid})
    assert '<span class="client">claude.ai</span>' in page.text
    login = client.post(
        "/oauth/login", data={"rid": rid, "secret": ADMIN_KEY}, follow_redirects=False
    )
    code = parse_qs(urlparse(login.headers["location"]).query)["code"][0]
    tokens = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": body["client_id"],
            "client_secret": body["client_secret"],
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text
    with_token = client.post(
        "/capture", json={}, headers={"Authorization": f"Bearer {tokens.json()['access_token']}"}
    )
    assert with_token.status_code == 503  # past the middleware


def test_empty_admin_key_keeps_the_server_open_and_mounts_no_oauth(
    monkeypatch: pytest.MonkeyPatch, restore_module: None
) -> None:
    module = _reload(monkeypatch, admin_key="")
    assert module._oauth_provider is None
    assert module._oauth_routes == []
    assert module._middleware == []
    client = _client(module)

    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/oauth/login",
        "/authorize",
    ):
        assert client.get(path).status_code == 404, path
    for path in ("/register", "/token", "/revoke"):
        assert client.post(path, data={}).status_code == 404, path

    # no auth at all: handlers are reached without any credential
    assert client.post("/capture", json={}).status_code == 503
    mcp = client.get("/mcp")
    assert mcp.status_code != 401
    assert "www-authenticate" not in mcp.headers
