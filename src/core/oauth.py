"""Built-in OAuth 2.1 authorization server for a single-owner Synatyx instance.

Implements `mcp.server.auth.provider.OAuthAuthorizationServerProvider` so the
MCP SDK's own `/authorize`, `/token`, `/register`, `/revoke` handlers and
`.well-known` metadata routes can be mounted as-is (see
`src/transports/mcp/oauth.py`). Nothing here speaks HTTP except by returning
redirect URLs the SDK handlers send the browser to.

Why it exists: clients that cannot attach a static header — above all
claude.ai custom connectors — need a real OAuth handshake. Synatyx has exactly
one principal (the owner), so "consent" is the owner proving they hold the
admin key (or `OAUTH_OWNER_PASSWORD`) on a minimal login page.

Storage split:
  * dynamic client registrations -> Postgres (must survive restarts, or every
    connector breaks on deploy)
  * authorization codes, access tokens, refresh tokens -> Redis with TTLs.
    Tokens are opaque random strings; only their SHA-256 hashes are stored, so
    a dump of Redis does not hand out credentials.

Abuse controls (registration is open because claude.ai needs dynamic client
registration): unused registrations expire, the client table is capped, the
owner login is throttled per parked request and per client IP, and codes and
parked requests are consumed atomically.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl, BaseModel

logger = logging.getLogger(__name__)

# Redis key namespaces. Values are JSON; every key carries a TTL.
KEY_PREFIX = "synatyx:oauth"
PENDING_KEY = KEY_PREFIX + ":pending:{}"   # login page handoff, keyed by request id
CODE_KEY = KEY_PREFIX + ":code:{}"         # keyed by sha256(authorization code)
ACCESS_KEY = KEY_PREFIX + ":access:{}"     # keyed by sha256(access token)
REFRESH_KEY = KEY_PREFIX + ":refresh:{}"   # keyed by sha256(refresh token)
FAILURES_KEY = KEY_PREFIX + ":login-failures:{}"  # wrong secrets per parked request
IP_ATTEMPTS_KEY = KEY_PREFIX + ":login-ip:{}"     # login attempts per client IP

# Single-principal server: every token belongs to the instance owner.
OWNER_SUBJECT = "owner"

DEFAULT_CODE_TTL_SECONDS = 300
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 3600
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600
DEFAULT_CLIENT_SECRET_TTL_SECONDS = 90 * 24 * 3600
DEFAULT_UNUSED_CLIENT_TTL_SECONDS = 24 * 3600
DEFAULT_MAX_CLIENTS = 200
DEFAULT_LOGIN_MAX_FAILURES = 5          # per parked request, then it is discarded
DEFAULT_LOGIN_MAX_PER_MINUTE = 10       # per client IP, then 429
LOGIN_IP_WINDOW_SECONDS = 60
# Bucket for requests without a peer address (uvicorn on a Unix socket, or an
# untrusted proxy): they share one limit rather than escaping it.
UNKNOWN_CLIENT = "unknown"

EXPIRED_REQUEST_MESSAGE = "This authorization request expired. Start again from your client."


# ---------------------------------------------------------------------------
# Storage ports — duck-typed so tests can pass fakes (no Redis/Postgres needed)
# ---------------------------------------------------------------------------

class OAuthClientRepository(Protocol):
    """Persistent store for dynamic client registrations (PostgresStorage)."""

    async def oauth_client_upsert(self, client_id: str, data: dict[str, Any]) -> None: ...

    async def oauth_client_get(self, client_id: str) -> dict[str, Any] | None: ...

    async def oauth_client_count(self) -> int: ...

    async def oauth_client_touch(self, client_id: str) -> None:
        """Record that the client obtained a token (it is no longer 'unused')."""
        ...

    async def oauth_client_prune_unused(self, older_than_seconds: int) -> int:
        """Delete registrations that never obtained a token and are older than the cutoff."""
        ...


class OAuthKeyValueStore(Protocol):
    """TTL'd key/value store for codes and tokens (RedisStorage)."""

    async def kv_set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    async def kv_get(self, key: str) -> str | None: ...

    async def kv_delete(self, key: str) -> int: ...

    async def kv_incr(self, key: str, ttl_seconds: int) -> int:
        """Atomically increment a counter that expires `ttl_seconds` after it was created."""
        ...


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

class PendingAuthorization(BaseModel):
    """An /authorize request parked in Redis while the owner logs in."""

    client_id: str
    client_name: str | None = None
    redirect_uri: AnyUrl
    redirect_origin: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    scopes: list[str]
    state: str | None = None
    resource: str | None = None
    expires_at: float


class StoredAccessToken(AccessToken):
    """`token` holds the SHA-256 hash, never the bearer value itself."""

    refresh_token_hash: str | None = None


class StoredRefreshToken(RefreshToken):
    """`token` holds the SHA-256 hash, never the refresh value itself."""

    access_token_hash: str | None = None
    # RFC 8707 audience the original grant was bound to; rotation keeps it.
    resource: str | None = None


class OAuthLoginError(Exception):
    """Owner login failed. `retryable` -> the login form can be re-offered."""

    status_code = 400

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        if retryable:
            self.status_code = 401


class OAuthThrottledError(OAuthLoginError):
    """Too many login attempts from this client IP (HTTP 429)."""

    status_code = 429

    def __init__(self, message: str = "Too many attempts. Try again in a minute.") -> None:
        super().__init__(message, retryable=False)
        self.status_code = 429


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_resource(value: str) -> str:
    """Canonical form of an RFC 8707 resource indicator for comparison."""
    parsed = urlparse(value)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def url_origin(uri: AnyUrl | str) -> str:
    """scheme://host[:port] of a URL — what the consent page shows the owner."""
    parsed = urlparse(str(uri))
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def redirect_uri_allowed(uri: AnyUrl) -> bool:
    """Plaintext HTTP redirects are only safe for loopback (native clients).

    Claude Code registers http://localhost:<port>/callback; claude.ai uses
    https://claude.ai/api/mcp/auth_callback. Custom app schemes (cursor://, …)
    are left to the client.
    """
    scheme = (uri.scheme or "").lower()
    if scheme == "http":
        return (uri.host or "").lower() in {"localhost", "127.0.0.1", "::1"}
    return True


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class SynatyxOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Single-owner OAuth 2.1 authorization server.

    Stores are optional at construction time because the HTTP app has to build
    its route table at import time, before the lifespan has opened Redis and
    Postgres connections; `bind()` injects them once they exist.
    """

    def __init__(
        self,
        *,
        public_url: str,
        owner_secrets: Sequence[str],
        scopes: Sequence[str] = ("synatyx",),
        login_path: str = "/oauth/login",
        code_ttl_seconds: int = DEFAULT_CODE_TTL_SECONDS,
        access_token_ttl_seconds: int = DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
        refresh_token_ttl_seconds: int = DEFAULT_REFRESH_TOKEN_TTL_SECONDS,
        client_secret_ttl_seconds: int = DEFAULT_CLIENT_SECRET_TTL_SECONDS,
        unused_client_ttl_seconds: int = DEFAULT_UNUSED_CLIENT_TTL_SECONDS,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        login_max_failures: int = DEFAULT_LOGIN_MAX_FAILURES,
        login_max_per_minute: int = DEFAULT_LOGIN_MAX_PER_MINUTE,
        clients: OAuthClientRepository | None = None,
        kv: OAuthKeyValueStore | None = None,
    ) -> None:
        self._public_url = public_url.rstrip("/")
        self._owner_secrets = [s for s in owner_secrets if s]
        self._scopes = list(scopes)
        self._login_path = login_path
        self._code_ttl = code_ttl_seconds
        self._access_ttl = access_token_ttl_seconds
        self._refresh_ttl = refresh_token_ttl_seconds
        self._client_secret_ttl = client_secret_ttl_seconds
        self._unused_client_ttl = unused_client_ttl_seconds
        self._max_clients = max_clients
        self._login_max_failures = login_max_failures
        self._login_max_per_minute = login_max_per_minute
        self._clients = clients
        self._kv = kv
        # RFC 8707 audience: tokens may only be bound to this server. Both the
        # bare base URL and the MCP endpoint are accepted because clients derive
        # the resource from the URL they were given.
        self._allowed_resources = {
            normalize_resource(self._public_url),
            normalize_resource(f"{self._public_url}/mcp"),
        }

    def bind(self, *, clients: OAuthClientRepository, kv: OAuthKeyValueStore) -> None:
        """Attach the live storage backends (called from the app lifespan)."""
        self._clients = clients
        self._kv = kv

    @property
    def scopes(self) -> list[str]:
        return list(self._scopes)

    @property
    def _client_store(self) -> OAuthClientRepository:
        if self._clients is None:  # pragma: no cover - wiring guard
            raise RuntimeError("OAuth client store not bound")
        return self._clients

    @property
    def _kv_store(self) -> OAuthKeyValueStore:
        if self._kv is None:  # pragma: no cover - wiring guard
            raise RuntimeError("OAuth key/value store not bound")
        return self._kv

    # -- owner authentication -------------------------------------------------

    def verify_owner(self, secret: str) -> bool:
        """Constant-time compare against every configured owner secret."""
        if not self._owner_secrets or not secret:
            return False
        provided = secret.encode()
        # Compare against every candidate (no short-circuit) so timing does not
        # reveal which secret matched.
        matched = False
        for candidate in self._owner_secrets:
            matched |= hmac.compare_digest(provided, candidate.encode())
        return matched

    def resource_allowed(self, resource: str | None) -> bool:
        """RFC 8707 audience check — None means the client sent no indicator."""
        return resource is None or normalize_resource(resource) in self._allowed_resources

    def resource_matches_grant(self, granted: str | None, presented: str | None) -> bool:
        """RFC 8707 on /token: a presented `resource` must be the one the code
        was issued for; with no resource at /authorize it must at least be ours."""
        if presented is None:
            return True
        if granted is None:
            return self.resource_allowed(presented)
        return normalize_resource(presented) == normalize_resource(granted)

    # -- client registration --------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = await self._client_store.oauth_client_get(client_id)
        if data is None:
            return None
        return OAuthClientInformationFull.model_validate(data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:  # pragma: no cover - SDK always sets it
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        if not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri", "at least one redirect_uri is required")
        for uri in client_info.redirect_uris:
            if not redirect_uri_allowed(uri):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"redirect_uri '{uri}' must use https (http is allowed for loopback only)",
                )

        # Registration is anonymous, so bound the table: drop registrations
        # that never finished a handshake, then refuse to grow past the cap.
        pruned = await self._client_store.oauth_client_prune_unused(self._unused_client_ttl)
        if pruned:
            logger.info("Pruned %d unused OAuth client registrations", pruned)
        if await self._client_store.oauth_client_count() >= self._max_clients:
            logger.warning(
                "OAuth client registration refused: cap of %d reached", self._max_clients
            )
            raise RegistrationError(
                "invalid_client_metadata",
                "registration limit reached; unused registrations expire after "
                f"{self._unused_client_ttl // 3600} hours, retry later",
            )

        # RFC 7591 §3.2.1: a secret without an expiry is a permanent credential.
        if client_info.client_secret and client_info.client_secret_expires_at is None:
            client_info.client_secret_expires_at = int(time.time()) + self._client_secret_ttl

        await self._client_store.oauth_client_upsert(
            client_info.client_id, client_info.model_dump(mode="json")
        )
        logger.info(
            "OAuth client registered: %s (%s)", client_info.client_id, client_info.client_name
        )

    # -- authorization --------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the owner login page."""
        if not self.resource_allowed(params.resource):
            # RFC 8707: refuse to mint a token for someone else's resource.
            raise AuthorizeError(
                "invalid_request", f"resource '{params.resource}' is not served by this server"
            )
        request_id = secrets.token_urlsafe(24)
        pending = PendingAuthorization(
            client_id=str(client.client_id),
            client_name=client.client_name,
            redirect_uri=params.redirect_uri,
            redirect_origin=url_origin(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            scopes=params.scopes if params.scopes is not None else list(self._scopes),
            state=params.state,
            resource=params.resource,
            expires_at=time.time() + self._code_ttl,
        )
        await self._kv_store.kv_set(
            PENDING_KEY.format(request_id), pending.model_dump_json(), self._code_ttl
        )
        return f"{self._public_url}{self._login_path}?rid={request_id}"

    async def get_pending(self, request_id: str) -> PendingAuthorization | None:
        """The parked request behind a login page, or None if unknown/expired."""
        if not request_id:
            return None
        raw = await self._kv_store.kv_get(PENDING_KEY.format(request_id))
        if raw is None:
            return None
        pending = PendingAuthorization.model_validate_json(raw)
        if pending.expires_at < time.time():
            await self._discard_pending(request_id)
            return None
        return pending

    async def _discard_pending(self, request_id: str) -> None:
        await self._kv_store.kv_delete(PENDING_KEY.format(request_id))
        await self._kv_store.kv_delete(FAILURES_KEY.format(request_id))

    async def complete_authorization(
        self, request_id: str, secret: str, *, client_ip: str | None = None
    ) -> str:
        """Owner submitted the login form: issue a code and return the redirect.

        Raises OAuthThrottledError when the client IP (or the shared "unknown"
        bucket when no address is known) is over its per-minute budget,
        OAuthLoginError when the secret is wrong (retryable, until the parked
        request has absorbed too many failures) or the parked request is
        unknown/expired (not retryable).
        """
        # A missing address must fail closed: everyone without one shares the
        # "unknown" bucket instead of bypassing the limit.
        bucket = client_ip or UNKNOWN_CLIENT
        attempts = await self._kv_store.kv_incr(
            IP_ATTEMPTS_KEY.format(bucket), LOGIN_IP_WINDOW_SECONDS
        )
        if attempts > self._login_max_per_minute:
            logger.warning("OAuth login throttled for %s", bucket)
            raise OAuthThrottledError()

        if not request_id:
            raise OAuthLoginError("Missing authorization request.", retryable=False)

        pending = await self.get_pending(request_id)
        if pending is None:
            raise OAuthLoginError(EXPIRED_REQUEST_MESSAGE, retryable=False)

        if not self.verify_owner(secret):
            failures = await self._kv_store.kv_incr(
                FAILURES_KEY.format(request_id), self._code_ttl
            )
            logger.warning(
                "OAuth owner login failed for client %s (%d/%d)",
                pending.client_id, failures, self._login_max_failures,
            )
            if failures >= self._login_max_failures:
                # Lock the parked request: the attacker must restart from
                # /authorize, and the owner gets a generic message.
                await self._discard_pending(request_id)
                raise OAuthLoginError(EXPIRED_REQUEST_MESSAGE, retryable=False)
            raise OAuthLoginError("Incorrect key.", retryable=True)

        # One pending request, one code: the delete is the atomic claim, so two
        # concurrent submissions cannot both issue a code.
        if await self._kv_store.kv_delete(PENDING_KEY.format(request_id)) == 0:
            raise OAuthLoginError(EXPIRED_REQUEST_MESSAGE, retryable=False)
        await self._kv_store.kv_delete(FAILURES_KEY.format(request_id))

        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            code=token_hash(code),
            scopes=pending.scopes,
            expires_at=time.time() + self._code_ttl,
            client_id=pending.client_id,
            code_challenge=pending.code_challenge,
            redirect_uri=pending.redirect_uri,
            redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
            resource=pending.resource,
            subject=OWNER_SUBJECT,
        )
        await self._kv_store.kv_set(
            CODE_KEY.format(record.code), record.model_dump_json(), self._code_ttl
        )
        logger.info("OAuth authorization code issued to client %s", pending.client_id)
        return construct_redirect_uri(
            str(pending.redirect_uri), code=code, state=pending.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = await self._kv_store.kv_get(CODE_KEY.format(token_hash(authorization_code)))
        if raw is None:
            return None
        record = AuthorizationCode.model_validate_json(raw)
        if record.client_id != client.client_id:
            return None
        return record

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Codes are single-use (RFC 6749 §10.5). The delete is the atomic claim:
        # concurrent exchanges both load the code, but only one delete returns 1.
        if await self._kv_store.kv_delete(CODE_KEY.format(authorization_code.code)) == 0:
            logger.warning("OAuth code replay attempt for client %s", client.client_id)
            raise TokenError("invalid_grant", "authorization code has already been used")
        if authorization_code.expires_at < time.time():
            raise TokenError("invalid_grant", "authorization code has expired")
        tokens = await self._issue_tokens(
            client_id=str(client.client_id),
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )
        await self._client_store.oauth_client_touch(str(client.client_id))
        return tokens

    # -- tokens ---------------------------------------------------------------

    async def _issue_tokens(
        self, *, client_id: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        access_hash = token_hash(access_token)
        refresh_hash = token_hash(refresh_token)
        now = int(time.time())

        access_record = StoredAccessToken(
            token=access_hash,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + self._access_ttl,
            resource=resource,
            subject=OWNER_SUBJECT,
            refresh_token_hash=refresh_hash,
        )
        refresh_record = StoredRefreshToken(
            token=refresh_hash,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + self._refresh_ttl,
            subject=OWNER_SUBJECT,
            access_token_hash=access_hash,
            resource=resource,
        )
        await self._kv_store.kv_set(
            ACCESS_KEY.format(access_hash), access_record.model_dump_json(), self._access_ttl
        )
        await self._kv_store.kv_set(
            REFRESH_KEY.format(refresh_hash), refresh_record.model_dump_json(), self._refresh_ttl
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self._access_ttl,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh_token,
        )

    async def load_access_token(self, token: str) -> StoredAccessToken | None:
        raw = await self._kv_store.kv_get(ACCESS_KEY.format(token_hash(token)))
        if raw is None:
            return None
        record = StoredAccessToken.model_validate_json(raw)
        if record.expires_at is not None and record.expires_at < int(time.time()):
            await self._kv_store.kv_delete(ACCESS_KEY.format(record.token))
            return None
        if not self.resource_allowed(record.resource):
            # Audience confusion guard: a token minted for another resource
            # (e.g. after PUBLIC_URL changed) must not authenticate this one.
            logger.warning("Rejected access token bound to foreign resource %s", record.resource)
            return None
        return record

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> StoredRefreshToken | None:
        raw = await self._kv_store.kv_get(REFRESH_KEY.format(token_hash(refresh_token)))
        if raw is None:
            return None
        record = StoredRefreshToken.model_validate_json(raw)
        if record.client_id != client.client_id:
            return None
        if record.expires_at is not None and record.expires_at < int(time.time()):
            await self.revoke_token(record)
            return None
        return record

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotation: the presented refresh token and the access token it was
        # issued with both die here (RFC 9700 §4.14.2). The audience binding
        # of the original grant is carried over, never widened.
        resource = refresh_token.resource if isinstance(refresh_token, StoredRefreshToken) else None
        await self.revoke_token(refresh_token)
        return await self._issue_tokens(
            client_id=str(client.client_id),
            scopes=scopes or refresh_token.scopes,
            resource=resource,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke a token and its counterpart (RFC 7009 recommendation).

        Params are typed with the SDK base models because the revocation
        handler calls this with whatever the loaders returned; the paired hash
        only exists on the Stored* subclasses this provider writes.
        """
        if isinstance(token, RefreshToken):
            await self._kv_store.kv_delete(REFRESH_KEY.format(token.token))
            paired = token.access_token_hash if isinstance(token, StoredRefreshToken) else None
            if paired:
                await self._kv_store.kv_delete(ACCESS_KEY.format(paired))
            return
        await self._kv_store.kv_delete(ACCESS_KEY.format(token.token))
        paired_refresh = token.refresh_token_hash if isinstance(token, StoredAccessToken) else None
        if paired_refresh:
            await self._kv_store.kv_delete(REFRESH_KEY.format(paired_refresh))
