from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import (
    AuthorizationParams,
    AuthorizeError,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from src.core.oauth import (
    ACCESS_KEY,
    CODE_KEY,
    FAILURES_KEY,
    IP_ATTEMPTS_KEY,
    PENDING_KEY,
    REFRESH_KEY,
    UNKNOWN_CLIENT,
    OAuthLoginError,
    OAuthThrottledError,
    SynatyxOAuthProvider,
    token_hash,
)

ADMIN_KEY = "admin-key-123"
OWNER_PASSWORD = "owner-pass-456"
REDIRECT_URI = "https://client.example.com/callback"
PUBLIC_URL = "https://memory.example.com"


# ---------------------------------------------------------------------------
# Fakes — the provider only needs a TTL'd KV store and a client repository
# ---------------------------------------------------------------------------

class FakeKV:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def kv_set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.store[key] = value
        self.ttls[key] = ttl_seconds

    async def kv_get(self, key: str) -> str | None:
        return self.store.get(key)

    async def kv_delete(self, key: str) -> int:
        self.ttls.pop(key, None)
        return 1 if self.store.pop(key, None) is not None else 0

    async def kv_incr(self, key: str, ttl_seconds: int) -> int:
        count = int(self.store.get(key, "0")) + 1
        self.store[key] = str(count)
        self.ttls.setdefault(key, ttl_seconds)
        return count


class BrokenKV(FakeKV):
    """Every call fails like a Redis outage does."""

    async def kv_get(self, key: str) -> str | None:
        raise ConnectionError("redis is down")

    async def kv_set(self, key: str, value: str, ttl_seconds: int) -> None:
        raise ConnectionError("redis is down")

    async def kv_incr(self, key: str, ttl_seconds: int) -> int:
        raise ConnectionError("redis is down")


class FakeClients:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.created_at: dict[str, float] = {}
        self.last_used_at: dict[str, float] = {}

    async def oauth_client_upsert(self, client_id: str, data: dict[str, Any]) -> None:
        self.rows[client_id] = data
        self.created_at.setdefault(client_id, time.time())

    async def oauth_client_get(self, client_id: str) -> dict[str, Any] | None:
        return self.rows.get(client_id)

    async def oauth_client_count(self) -> int:
        return len(self.rows)

    async def oauth_client_touch(self, client_id: str) -> None:
        self.last_used_at[client_id] = time.time()

    async def oauth_client_prune_unused(self, older_than_seconds: int) -> int:
        cutoff = time.time() - older_than_seconds
        stale = [
            cid for cid, created in self.created_at.items()
            if cid not in self.last_used_at and created < cutoff
        ]
        for cid in stale:
            self.rows.pop(cid, None)
            self.created_at.pop(cid, None)
        return len(stale)


def make_provider(**kwargs: Any) -> tuple[SynatyxOAuthProvider, FakeKV, FakeClients]:
    kv, clients = FakeKV(), FakeClients()
    provider = SynatyxOAuthProvider(
        public_url=PUBLIC_URL,
        owner_secrets=[ADMIN_KEY, OWNER_PASSWORD],
        scopes=["synatyx"],
        clients=clients,
        kv=kv,
        **kwargs,
    )
    return provider, kv, clients


def make_client(
    client_id: str = "client-1", *, redirect_uri: str | None = REDIRECT_URI
) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="client-secret",
        redirect_uris=[AnyUrl(redirect_uri)] if redirect_uri else None,
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="synatyx",
        token_endpoint_auth_method="client_secret_post",
        client_name="claude.ai",
    )


def pkce_pair() -> tuple[str, str]:
    verifier = "v" * 64
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def park_authorization(
    provider: SynatyxOAuthProvider,
    client: OAuthClientInformationFull,
    challenge: str,
    state: str | None = "state-xyz",
) -> str:
    url = await provider.authorize(
        client,
        AuthorizationParams(
            state=state,
            scopes=["synatyx"],
            code_challenge=challenge,
            redirect_uri=AnyUrl(REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
            resource=f"{PUBLIC_URL}/mcp",
        ),
    )
    return parse_qs(urlparse(url).query)["rid"][0]


async def issue_code(
    provider: SynatyxOAuthProvider,
    client: OAuthClientInformationFull,
    challenge: str,
    secret: str = ADMIN_KEY,
) -> str:
    rid = await park_authorization(provider, client, challenge)
    redirect = await provider.complete_authorization(rid, secret)
    return parse_qs(urlparse(redirect).query)["code"][0]


# ---------------------------------------------------------------------------
# Dynamic client registration
# ---------------------------------------------------------------------------

async def test_register_client_persists_and_reloads() -> None:
    provider, _, clients = make_provider()
    await provider.register_client(make_client())

    assert "client-1" in clients.rows
    assert clients.rows["client-1"]["client_name"] == "claude.ai"

    loaded = await provider.get_client("client-1")
    assert loaded is not None
    assert loaded.client_secret == "client-secret"
    assert loaded.redirect_uris is not None
    assert str(loaded.redirect_uris[0]) == REDIRECT_URI


async def test_get_client_unknown_returns_none() -> None:
    provider, _, _ = make_provider()
    assert await provider.get_client("nope") is None


async def test_register_client_without_redirect_uri_is_rejected() -> None:
    provider, _, _ = make_provider()
    with pytest.raises(RegistrationError):
        await provider.register_client(make_client(redirect_uri=None))


async def test_register_client_rejects_plaintext_redirect_uri() -> None:
    """http is only acceptable for loopback — native clients (Claude Code)."""
    provider, _, _ = make_provider()
    with pytest.raises(RegistrationError):
        await provider.register_client(make_client(redirect_uri="http://evil.example.com/cb"))

    await provider.register_client(
        make_client("local", redirect_uri="http://localhost:51763/callback")
    )
    assert await provider.get_client("local") is not None


# ---------------------------------------------------------------------------
# Authorize + owner login
# ---------------------------------------------------------------------------

async def test_authorize_parks_request_and_points_at_login_page() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()

    url = await provider.authorize(
        make_client(),
        AuthorizationParams(
            state="state-xyz",
            scopes=None,
            code_challenge=challenge,
            redirect_uri=AnyUrl(REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
        ),
    )

    assert url.startswith(f"{PUBLIC_URL}/oauth/login?rid=")
    rid = parse_qs(urlparse(url).query)["rid"][0]
    pending = json.loads(kv.store[PENDING_KEY.format(rid)])
    assert pending["client_id"] == "client-1"
    assert pending["code_challenge"] == challenge
    assert pending["scopes"] == ["synatyx"]  # falls back to the configured scope
    assert kv.ttls[PENDING_KEY.format(rid)] == 300


async def test_login_with_wrong_secret_is_retryable_and_keeps_request() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    rid = await park_authorization(provider, make_client(), challenge)

    with pytest.raises(OAuthLoginError) as exc:
        await provider.complete_authorization(rid, "not-the-key")

    assert exc.value.retryable is True
    assert PENDING_KEY.format(rid) in kv.store  # owner may try again
    assert not [k for k in kv.store if k.startswith("synatyx:oauth:code:")]


async def test_login_with_admin_key_issues_code_bound_to_request() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    rid = await park_authorization(provider, make_client(), challenge)

    redirect = await provider.complete_authorization(rid, ADMIN_KEY)

    query = parse_qs(urlparse(redirect).query)
    assert redirect.startswith(REDIRECT_URI)
    assert query["state"] == ["state-xyz"]
    code = query["code"][0]
    # the pending request is consumed, and only the hash of the code is stored
    assert PENDING_KEY.format(rid) not in kv.store
    assert CODE_KEY.format(token_hash(code)) in kv.store
    assert code not in json.dumps(kv.store)
    record = json.loads(kv.store[CODE_KEY.format(token_hash(code))])
    assert record["client_id"] == "client-1"
    assert record["code_challenge"] == challenge
    assert record["redirect_uri"] == REDIRECT_URI
    assert record["resource"] == f"{PUBLIC_URL}/mcp"


async def test_login_accepts_owner_password_too() -> None:
    provider, _, _ = make_provider()
    _, challenge = pkce_pair()
    rid = await park_authorization(provider, make_client(), challenge)

    redirect = await provider.complete_authorization(rid, OWNER_PASSWORD)
    assert "code=" in redirect


async def test_login_with_unknown_request_is_not_retryable() -> None:
    provider, _, _ = make_provider()
    with pytest.raises(OAuthLoginError) as exc:
        await provider.complete_authorization("does-not-exist", ADMIN_KEY)
    assert exc.value.retryable is False


async def test_login_with_expired_pending_request_fails() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    rid = await park_authorization(provider, make_client(), challenge)
    pending = json.loads(kv.store[PENDING_KEY.format(rid)])
    pending["expires_at"] = time.time() - 1
    kv.store[PENDING_KEY.format(rid)] = json.dumps(pending)

    with pytest.raises(OAuthLoginError) as exc:
        await provider.complete_authorization(rid, ADMIN_KEY)
    assert exc.value.retryable is False
    assert PENDING_KEY.format(rid) not in kv.store


async def test_authorize_rejects_foreign_resource_indicator() -> None:
    """RFC 8707: do not mint tokens for a resource this server does not serve."""
    provider, _, _ = make_provider()
    _, challenge = pkce_pair()

    with pytest.raises(AuthorizeError):
        await provider.authorize(
            make_client(),
            AuthorizationParams(
                state=None,
                scopes=["synatyx"],
                code_challenge=challenge,
                redirect_uri=AnyUrl(REDIRECT_URI),
                redirect_uri_provided_explicitly=True,
                resource="https://someone-else.example.com/mcp",
            ),
        )
    # the server's own identifiers are accepted, with or without trailing slash
    assert provider.resource_allowed(f"{PUBLIC_URL}/mcp/") is True
    assert provider.resource_allowed(PUBLIC_URL) is True
    assert provider.resource_allowed(None) is True


async def test_access_token_bound_to_foreign_resource_is_rejected() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    tokens = await provider.exchange_authorization_code(client, record)

    key = ACCESS_KEY.format(token_hash(tokens.access_token))
    stored = json.loads(kv.store[key])
    stored["resource"] = "https://someone-else.example.com/mcp"
    kv.store[key] = json.dumps(stored)

    assert await provider.load_access_token(tokens.access_token) is None


async def test_verify_owner_rejects_empty_and_unset_secrets() -> None:
    provider, _, _ = make_provider()
    assert provider.verify_owner("") is False

    no_secret = SynatyxOAuthProvider(public_url=PUBLIC_URL, owner_secrets=["", ""])
    assert no_secret.verify_owner("anything") is False


# ---------------------------------------------------------------------------
# Code exchange + PKCE
# ---------------------------------------------------------------------------

async def test_stored_challenge_matches_s256_of_verifier() -> None:
    """The SDK's token handler compares S256(verifier) to the stored challenge."""
    provider, kv, _ = make_provider()
    verifier, challenge = pkce_pair()
    code = await issue_code(provider, make_client(), challenge)

    record = json.loads(kv.store[CODE_KEY.format(token_hash(code))])
    recomputed = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    assert record["code_challenge"] == recomputed


async def test_load_authorization_code_rejects_other_client() -> None:
    provider, _, _ = make_provider()
    _, challenge = pkce_pair()
    code = await issue_code(provider, make_client(), challenge)

    assert await provider.load_authorization_code(make_client("client-2"), code) is None
    assert await provider.load_authorization_code(make_client(), code) is not None
    assert await provider.load_authorization_code(make_client(), "bogus-code") is None


async def test_code_exchange_issues_tokens_and_burns_the_code() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)

    record = await provider.load_authorization_code(client, code)
    assert record is not None
    tokens = await provider.exchange_authorization_code(client, record)

    assert tokens.token_type == "Bearer"
    assert tokens.expires_in == 3600
    assert tokens.scope == "synatyx"
    assert tokens.refresh_token is not None
    # single use: the code is gone
    assert await provider.load_authorization_code(client, code) is None
    # tokens are stored hashed only
    assert tokens.access_token not in json.dumps(kv.store)
    assert ACCESS_KEY.format(token_hash(tokens.access_token)) in kv.store

    access = await provider.load_access_token(tokens.access_token)
    assert access is not None
    assert access.client_id == "client-1"
    assert access.scopes == ["synatyx"]
    assert access.subject == "owner"


async def test_expired_code_cannot_be_exchanged() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    key = CODE_KEY.format(token_hash(code))
    record = json.loads(kv.store[key])
    record["expires_at"] = time.time() - 1
    kv.store[key] = json.dumps(record)

    loaded = await provider.load_authorization_code(client, code)
    assert loaded is not None
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, loaded)
    assert key not in kv.store


# ---------------------------------------------------------------------------
# Tokens: expiry, refresh rotation, revocation
# ---------------------------------------------------------------------------

async def test_expired_access_token_is_rejected_and_dropped() -> None:
    provider, kv, _ = make_provider(access_token_ttl_seconds=1)
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    tokens = await provider.exchange_authorization_code(client, record)

    key = ACCESS_KEY.format(token_hash(tokens.access_token))
    stored = json.loads(kv.store[key])
    stored["expires_at"] = int(time.time()) - 5
    kv.store[key] = json.dumps(stored)

    assert await provider.load_access_token(tokens.access_token) is None
    assert key not in kv.store


async def test_refresh_rotates_both_tokens() -> None:
    provider, _, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    first = await provider.exchange_authorization_code(client, record)
    assert first.refresh_token is not None

    loaded = await provider.load_refresh_token(client, first.refresh_token)
    assert loaded is not None
    second = await provider.exchange_refresh_token(client, loaded, ["synatyx"])

    assert second.access_token != first.access_token
    assert second.refresh_token != first.refresh_token
    # the rotated pair is dead: old refresh AND the access token it came with
    assert await provider.load_refresh_token(client, first.refresh_token) is None
    assert await provider.load_access_token(first.access_token) is None
    assert await provider.load_access_token(second.access_token) is not None


async def test_refresh_token_of_other_client_is_invisible() -> None:
    provider, _, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    tokens = await provider.exchange_authorization_code(client, record)
    assert tokens.refresh_token is not None

    assert await provider.load_refresh_token(make_client("client-2"), tokens.refresh_token) is None


async def test_expired_refresh_token_is_rejected() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    tokens = await provider.exchange_authorization_code(client, record)
    assert tokens.refresh_token is not None
    key = REFRESH_KEY.format(token_hash(tokens.refresh_token))
    stored = json.loads(kv.store[key])
    stored["expires_at"] = int(time.time()) - 5
    kv.store[key] = json.dumps(stored)

    assert await provider.load_refresh_token(client, tokens.refresh_token) is None
    assert key not in kv.store


async def test_revoking_access_token_also_revokes_its_refresh_token() -> None:
    provider, _, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    tokens = await provider.exchange_authorization_code(client, record)
    assert tokens.refresh_token is not None

    access = await provider.load_access_token(tokens.access_token)
    assert access is not None
    await provider.revoke_token(access)

    assert await provider.load_access_token(tokens.access_token) is None
    assert await provider.load_refresh_token(client, tokens.refresh_token) is None


async def test_refresh_ttl_is_configurable_in_days() -> None:
    provider, kv, _ = make_provider(refresh_token_ttl_seconds=30 * 24 * 3600)
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    tokens = await provider.exchange_authorization_code(client, record)
    assert tokens.refresh_token is not None

    assert kv.ttls[REFRESH_KEY.format(token_hash(tokens.refresh_token))] == 2_592_000
    assert kv.ttls[ACCESS_KEY.format(token_hash(tokens.access_token))] == 3600


# ---------------------------------------------------------------------------
# Review fixes: atomic consumption (F1), consent metadata (F2), throttling (F3),
# registration cap/expiry (F4), refresh keeps audience (F5)
# ---------------------------------------------------------------------------

async def test_code_exchanged_twice_yields_exactly_one_token_pair() -> None:
    """Two /token calls that both loaded the code before either burned it."""
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None

    first = await provider.exchange_authorization_code(client, record)
    with pytest.raises(TokenError) as exc:
        await provider.exchange_authorization_code(client, record)

    assert exc.value.error == "invalid_grant"
    assert len([k for k in kv.store if k.startswith("synatyx:oauth:access:")]) == 1
    assert await provider.load_access_token(first.access_token) is not None


async def test_parked_request_completed_twice_issues_one_code() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    rid = await park_authorization(provider, make_client(), challenge)

    # simulate the second submission having passed the lookup: reinsert the
    # pending record after the first completion consumed it, then delete it
    # underneath the second call exactly as a concurrent winner would
    raw = kv.store[PENDING_KEY.format(rid)]
    await provider.complete_authorization(rid, ADMIN_KEY)
    kv.store[PENDING_KEY.format(rid)] = raw
    original_delete = kv.kv_delete

    async def racing_delete(key: str) -> int:
        await original_delete(key)
        return 0  # the other request won the delete

    kv.kv_delete = racing_delete  # type: ignore[method-assign]
    with pytest.raises(OAuthLoginError) as exc:
        await provider.complete_authorization(rid, ADMIN_KEY)
    assert exc.value.retryable is False
    assert len([k for k in kv.store if k.startswith("synatyx:oauth:code:")]) == 1


async def test_pending_request_records_client_name_and_redirect_origin() -> None:
    provider, _, _ = make_provider()
    _, challenge = pkce_pair()
    rid = await park_authorization(provider, make_client(), challenge)

    pending = await provider.get_pending(rid)
    assert pending is not None
    assert pending.client_name == "claude.ai"
    assert pending.redirect_origin == "https://client.example.com"
    assert await provider.get_pending("unknown") is None


async def test_parked_request_is_discarded_after_too_many_wrong_secrets() -> None:
    provider, kv, _ = make_provider(login_max_failures=3)
    _, challenge = pkce_pair()
    rid = await park_authorization(provider, make_client(), challenge)

    for _ in range(2):
        with pytest.raises(OAuthLoginError) as exc:
            await provider.complete_authorization(rid, "wrong")
        assert exc.value.retryable is True
    assert kv.store[FAILURES_KEY.format(rid)] == "2"
    assert kv.ttls[FAILURES_KEY.format(rid)] == 300

    with pytest.raises(OAuthLoginError) as exc:
        await provider.complete_authorization(rid, "wrong")
    assert exc.value.retryable is False
    assert PENDING_KEY.format(rid) not in kv.store
    assert FAILURES_KEY.format(rid) not in kv.store
    # even the right secret cannot revive it
    with pytest.raises(OAuthLoginError):
        await provider.complete_authorization(rid, ADMIN_KEY)


async def test_login_attempts_per_ip_are_limited() -> None:
    provider, kv, _ = make_provider(login_max_per_minute=3)
    _, challenge = pkce_pair()
    client = make_client()

    for _ in range(3):
        rid = await park_authorization(provider, client, challenge)
        with pytest.raises(OAuthLoginError):
            await provider.complete_authorization(rid, "wrong", client_ip="203.0.113.7")
    assert kv.ttls[IP_ATTEMPTS_KEY.format("203.0.113.7")] == 60

    rid = await park_authorization(provider, client, challenge)
    with pytest.raises(OAuthThrottledError) as exc:
        await provider.complete_authorization(rid, ADMIN_KEY, client_ip="203.0.113.7")
    assert exc.value.status_code == 429
    # the right secret from another address still works; the limit is per IP
    redirect = await provider.complete_authorization(rid, ADMIN_KEY, client_ip="198.51.100.9")
    assert "code=" in redirect


async def test_registration_is_capped() -> None:
    provider, _, clients = make_provider(max_clients=2)
    await provider.register_client(make_client("a"))
    await provider.register_client(make_client("b"))

    with pytest.raises(RegistrationError) as exc:
        await provider.register_client(make_client("c"))
    assert exc.value.error == "invalid_client_metadata"
    assert "limit" in (exc.value.error_description or "")
    assert set(clients.rows) == {"a", "b"}


async def test_unused_registrations_expire_but_used_ones_stay() -> None:
    provider, _, clients = make_provider(unused_client_ttl_seconds=3600)
    await provider.register_client(make_client("stale"))
    await provider.register_client(make_client("active"))
    clients.created_at["stale"] = time.time() - 7200
    clients.created_at["active"] = time.time() - 7200
    await clients.oauth_client_touch("active")

    await provider.register_client(make_client("fresh"))

    assert set(clients.rows) == {"active", "fresh"}


async def test_token_issuance_marks_the_registration_used() -> None:
    provider, _, clients = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    await provider.register_client(client)
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    assert "client-1" not in clients.last_used_at

    await provider.exchange_authorization_code(client, record)

    assert "client-1" in clients.last_used_at


async def test_registered_client_secret_gets_an_expiry() -> None:
    provider, _, clients = make_provider(client_secret_ttl_seconds=3600)
    client = make_client()
    assert client.client_secret_expires_at is None

    await provider.register_client(client)

    expires = clients.rows["client-1"]["client_secret_expires_at"]
    assert expires is not None
    assert int(time.time()) + 3500 <= expires <= int(time.time()) + 3600
    # public clients (no secret) get none
    public = make_client("public")
    public.client_secret = None
    await provider.register_client(public)
    assert clients.rows["public"]["client_secret_expires_at"] is None


async def test_refreshed_token_keeps_the_audience_binding() -> None:
    provider, kv, _ = make_provider()
    _, challenge = pkce_pair()
    client = make_client()
    code = await issue_code(provider, client, challenge)
    record = await provider.load_authorization_code(client, code)
    assert record is not None
    first = await provider.exchange_authorization_code(client, record)
    assert first.refresh_token is not None

    loaded = await provider.load_refresh_token(client, first.refresh_token)
    assert loaded is not None
    assert loaded.resource == f"{PUBLIC_URL}/mcp"
    second = await provider.exchange_refresh_token(client, loaded, ["synatyx"])

    stored = json.loads(kv.store[ACCESS_KEY.format(token_hash(second.access_token))])
    assert stored["resource"] == f"{PUBLIC_URL}/mcp"
    # after PUBLIC_URL changes, the refreshed token is rejected like the original
    moved = SynatyxOAuthProvider(
        public_url="https://elsewhere.example.com", owner_secrets=[ADMIN_KEY], kv=kv
    )
    assert await moved.load_access_token(second.access_token) is None
    assert await provider.load_access_token(second.access_token) is not None


def test_token_request_resource_must_match_the_grant() -> None:
    provider, _, _ = make_provider()
    granted = f"{PUBLIC_URL}/mcp"
    assert provider.resource_matches_grant(granted, None) is True
    assert provider.resource_matches_grant(granted, f"{PUBLIC_URL}/mcp/") is True
    assert provider.resource_matches_grant(granted, PUBLIC_URL) is False
    assert provider.resource_matches_grant(None, PUBLIC_URL) is True
    assert provider.resource_matches_grant(None, "https://someone-else.example.com") is False


async def test_login_without_a_client_address_shares_the_unknown_bucket() -> None:
    """No peer address (uvicorn on a Unix socket) must fail closed, not open."""
    provider, kv, _ = make_provider(login_max_per_minute=2)
    _, challenge = pkce_pair()
    client = make_client()

    for missing in (None, ""):
        rid = await park_authorization(provider, client, challenge)
        with pytest.raises(OAuthLoginError):
            await provider.complete_authorization(rid, "wrong", client_ip=missing)
    assert kv.store[IP_ATTEMPTS_KEY.format(UNKNOWN_CLIENT)] == "2"

    rid = await park_authorization(provider, client, challenge)
    with pytest.raises(OAuthThrottledError):
        await provider.complete_authorization(rid, ADMIN_KEY, client_ip=None)
    # a caller with a real address has its own bucket
    redirect = await provider.complete_authorization(rid, ADMIN_KEY, client_ip="198.51.100.9")
    assert "code=" in redirect
