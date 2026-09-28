"""HTTP-level walk through the OAuth handshake, exactly as a connector does it:
register -> authorize -> owner login -> token -> authenticated call.

Uses the real SDK route handlers and the real auth middleware; only Redis and
Postgres are faked.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp.server.auth.provider import ProviderTokenVerifier
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.oauth import SynatyxOAuthProvider
from src.transports.mcp.http_server import AdminKeyAuthMiddleware
from src.transports.mcp.oauth import (
    PUBLIC_OAUTH_PATHS,
    PUBLIC_OAUTH_PREFIXES,
    build_auth_settings,
    build_oauth_routes,
    resource_metadata_url,
)
from tests.test_oauth import BrokenKV, FakeClients, FakeKV, pkce_pair

ADMIN_KEY = "admin-key-123"
PUBLIC_URL = "http://localhost:9000"  # the SDK only allows non-HTTPS on localhost
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
SCOPES = ["synatyx"]


async def protected(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


class NoPeerAddress:
    """ASGI wrapper that drops scope["client"], as uvicorn does on a Unix socket."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope["client"] = None
        await self.app(scope, receive, send)


def build_client(
    kv: FakeKV | None = None, *, no_peer_address: bool = False, **provider_kwargs: Any
) -> TestClient:
    provider = SynatyxOAuthProvider(
        public_url=PUBLIC_URL,
        owner_secrets=[ADMIN_KEY],
        scopes=SCOPES,
        clients=FakeClients(),
        kv=kv or FakeKV(),
        **provider_kwargs,
    )
    routes = build_oauth_routes(provider, build_auth_settings(PUBLIC_URL, SCOPES))
    app = Starlette(
        routes=[*routes, Route("/protected", protected)],
        middleware=[
            Middleware(
                AdminKeyAuthMiddleware,
                admin_key=ADMIN_KEY,
                header_name="X-Auth-Key",
                public_paths=frozenset({"/health"}) | PUBLIC_OAUTH_PATHS,
                public_prefixes=PUBLIC_OAUTH_PREFIXES,
                token_verifier=ProviderTokenVerifier(provider),
                resource_metadata_url=resource_metadata_url(PUBLIC_URL),
            )
        ],
    )
    return TestClient(NoPeerAddress(app) if no_peer_address else app)


def register_client(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "client_name": "claude.ai",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["client_id"], body["client_secret"]


def authorize_and_login(client: TestClient, client_id: str, challenge: str) -> str:
    """Run /authorize + the owner login form, return the authorization code."""
    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-1",
            "scope": "synatyx",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    login_url = response.headers["location"]
    assert login_url.startswith(f"{PUBLIC_URL}/oauth/login?rid=")
    rid = parse_qs(urlparse(login_url).query)["rid"][0]

    page = client.get("/oauth/login", params={"rid": rid})
    assert page.status_code == 200
    assert "Authorize access to Synatyx" in page.text
    # the owner can see who is asking and where the code will go
    assert '<span class="client">claude.ai</span>' in page.text
    assert '<span class="origin">https://claude.ai</span>' in page.text
    assert ">Authorize claude.ai</button>" in page.text

    submitted = client.post(
        "/oauth/login", data={"rid": rid, "secret": ADMIN_KEY}, follow_redirects=False
    )
    assert submitted.status_code == 302
    location = submitted.headers["location"]
    assert location.startswith(REDIRECT_URI)
    query = parse_qs(urlparse(location).query)
    assert query["state"] == ["state-1"]
    return query["code"][0]


def test_protected_route_without_credentials_returns_401_challenge() -> None:
    client = build_client()
    response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert response.headers["www-authenticate"] == (
        'Bearer resource_metadata="http://localhost:9000/.well-known/oauth-protected-resource"'
    )


def test_discovery_documents_are_public() -> None:
    client = build_client()

    metadata = client.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    body = metadata.json()
    assert body["issuer"].rstrip("/") == PUBLIC_URL
    assert body["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
    assert body["token_endpoint"] == f"{PUBLIC_URL}/token"
    assert body["registration_endpoint"] == f"{PUBLIC_URL}/register"
    assert body["revocation_endpoint"] == f"{PUBLIC_URL}/revoke"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]

    resource = client.get("/.well-known/oauth-protected-resource")
    assert resource.status_code == 200
    assert resource.json()["authorization_servers"][0].rstrip("/") == PUBLIC_URL

    # RFC 9728 path insertion variant, for clients whose resource is <base>/mcp
    mcp_resource = client.get("/.well-known/oauth-protected-resource/mcp")
    assert mcp_resource.status_code == 200
    assert mcp_resource.json()["resource"] == f"{PUBLIC_URL}/mcp"


def test_register_authorize_token_then_authenticated_call() -> None:
    client = build_client()
    verifier, challenge = pkce_pair()
    client_id, client_secret = register_client(client)
    code = authorize_and_login(client, client_id, challenge)

    token_response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == 200, token_response.text
    tokens = token_response.json()
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 3600
    assert tokens["scope"] == "synatyx"

    call = client.get("/protected", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert call.status_code == 200
    assert call.json() == {"status": "ok"}

    # the code cannot be replayed
    replay = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"

    # refresh rotates the pair; the old access token stops working
    refreshed = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    new_tokens = refreshed.json()
    assert new_tokens["access_token"] != tokens["access_token"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    assert client.get(
        "/protected", headers={"Authorization": f"Bearer {new_tokens['access_token']}"}
    ).status_code == 200
    assert client.get(
        "/protected", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    ).status_code == 401


def test_wrong_owner_secret_does_not_issue_a_code() -> None:
    client = build_client()
    _, challenge = pkce_pair()
    client_id, _ = register_client(client)

    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-1",
        },
        follow_redirects=False,
    )
    rid = parse_qs(urlparse(response.headers["location"]).query)["rid"][0]

    denied = client.post(
        "/oauth/login", data={"rid": rid, "secret": "wrong"}, follow_redirects=False
    )
    assert denied.status_code == 401
    assert "Incorrect key." in denied.text


def test_token_endpoint_rejects_wrong_pkce_verifier() -> None:
    client = build_client()
    _, challenge = pkce_pair()
    client_id, client_secret = register_client(client)
    code = authorize_and_login(client, client_id, challenge)

    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": "x" * 64,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_admin_key_still_authenticates_alongside_oauth() -> None:
    client = build_client()

    assert client.get("/protected", headers={"X-Auth-Key": ADMIN_KEY}).status_code == 200
    assert client.get(
        "/protected", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
    ).status_code == 200
    assert client.get("/protected", headers={"X-Auth-Key": "nope"}).status_code == 401


def test_login_page_without_request_id_is_a_bad_request() -> None:
    client = build_client()
    response = client.get("/oauth/login")
    assert response.status_code == 400
    assert "Missing authorization request." in response.text


def test_login_page_names_the_client_even_after_a_wrong_secret() -> None:
    client = build_client()
    _, challenge = pkce_pair()
    client_id, _ = register_client(client)
    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    rid = parse_qs(urlparse(response.headers["location"]).query)["rid"][0]

    denied = client.post("/oauth/login", data={"rid": rid, "secret": "wrong"})
    assert denied.status_code == 401
    assert "Incorrect key." in denied.text
    assert '<span class="client">claude.ai</span>' in denied.text
    assert f'name="rid" value="{rid}"' in denied.text


def test_login_is_throttled_per_ip_with_429() -> None:
    client = build_client(login_max_per_minute=2)
    _, challenge = pkce_pair()
    client_id, _ = register_client(client)

    def park() -> str:
        response = client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        return parse_qs(urlparse(response.headers["location"]).query)["rid"][0]

    for _ in range(2):
        denied = client.post("/oauth/login", data={"rid": park(), "secret": "wrong"})
        assert denied.status_code == 401

    throttled = client.post("/oauth/login", data={"rid": park(), "secret": ADMIN_KEY})
    assert throttled.status_code == 429
    assert "Too many attempts" in throttled.text


def test_token_endpoint_rejects_resource_that_differs_from_the_grant() -> None:
    client = build_client()
    verifier, challenge = pkce_pair()
    client_id, client_secret = register_client(client)
    code = authorize_and_login(client, client_id, challenge)
    base = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    }

    mismatch = client.post("/token", data={**base, "resource": "https://other.example.com/mcp"})
    assert mismatch.status_code == 400
    assert mismatch.json()["error"] == "invalid_target"

    # the code survives the refused attempt and works with our own resource
    ok = client.post("/token", data={**base, "resource": f"{PUBLIC_URL}/mcp"})
    assert ok.status_code == 200, ok.text


def test_redis_outage_yields_503_on_oauth_routes_and_401_on_protected_ones() -> None:
    client = build_client(kv=BrokenKV())
    _, challenge = pkce_pair()
    client_id, client_secret = register_client(client)  # clients live in Postgres, still fine

    authorize = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    # the SDK's catch-all sends the client a clean error redirect, not a traceback
    assert authorize.status_code == 302
    assert parse_qs(urlparse(authorize.headers["location"]).query)["error"] == ["server_error"]

    login = client.post("/oauth/login", data={"rid": "any", "secret": ADMIN_KEY})
    assert login.status_code == 503
    assert "cannot reach its storage" in login.text

    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": "any",
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": "x" * 64,
        },
    )
    assert token.status_code == 503
    assert token.json()["error"] == "temporarily_unavailable"

    protected = client.get("/protected", headers={"Authorization": "Bearer some-token"})
    assert protected.status_code == 401
    assert client.get("/protected", headers={"X-Auth-Key": ADMIN_KEY}).status_code == 200


def test_login_without_a_peer_address_is_still_throttled() -> None:
    """uvicorn on a Unix socket leaves request.client None; the eleventh attempt is 429."""
    client = build_client(no_peer_address=True)
    _, challenge = pkce_pair()
    client_id, _ = register_client(client)

    def park() -> str:
        response = client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        return parse_qs(urlparse(response.headers["location"]).query)["rid"][0]

    for _ in range(10):
        denied = client.post("/oauth/login", data={"rid": park(), "secret": "wrong"})
        assert denied.status_code == 401

    eleventh = client.post("/oauth/login", data={"rid": park(), "secret": ADMIN_KEY})
    assert eleventh.status_code == 429
    assert "Too many attempts" in eleventh.text
