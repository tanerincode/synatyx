"""HTTP wiring for the built-in OAuth 2.1 authorization server.

Everything here is glue: the protocol endpoints (`/authorize`, `/token`,
`/register`, `/revoke`, `/.well-known/oauth-authorization-server`,
`/.well-known/oauth-protected-resource`) come from
`mcp.server.auth.routes.create_auth_routes` / `create_protected_resource_routes`
and are configured through `mcp.server.auth.settings.AuthSettings`. Synatyx adds
three things on top: the owner login page the provider redirects to, an RFC 8707
`resource` check on `/token` (the SDK does not compare it with the code's), and
a storage guard so a Redis/Postgres outage answers 503 instead of a traceback.
"""
from __future__ import annotations

import html
import logging

from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.routes import (
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.oauth import OAuthLoginError, PendingAuthorization, SynatyxOAuthProvider

logger = logging.getLogger(__name__)

LOGIN_PATH = "/oauth/login"
TOKEN_PATH = "/token"
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"

# Paths the admin-key middleware must let through unauthenticated: the OAuth
# handshake itself cannot require the credential it is there to hand out.
PUBLIC_OAUTH_PATHS = frozenset({"/authorize", TOKEN_PATH, "/register", "/revoke", LOGIN_PATH})
# Discovery documents are public by specification (RFC 8414 / RFC 9728).
PUBLIC_OAUTH_PREFIXES = ("/.well-known/",)

STORAGE_UNAVAILABLE = "Synatyx cannot reach its storage right now. Try again in a moment."


def build_auth_settings(
    public_url: str, scopes: list[str], *, client_secret_ttl_seconds: int | None = None
) -> AuthSettings:
    """SDK auth configuration derived from PUBLIC_URL.

    PUBLIC_URL is authoritative for the issuer: behind Caddy the forwarded
    headers can be spoofed, and an issuer that disagrees with the URL the
    client typed makes the client reject the metadata document.
    """
    base = public_url.rstrip("/")
    return AuthSettings(
        issuer_url=AnyHttpUrl(base),
        resource_server_url=AnyHttpUrl(base),
        client_registration_options=ClientRegistrationOptions(
            # claude.ai registers itself on first connect — without this the
            # connector cannot be set up at all.
            enabled=True,
            client_secret_expiry_seconds=client_secret_ttl_seconds,
            valid_scopes=scopes,
            default_scopes=scopes,
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[],
    )


def resource_metadata_url(public_url: str) -> str:
    return f"{public_url.rstrip('/')}{PROTECTED_RESOURCE_PATH}"


def build_oauth_routes(provider: SynatyxOAuthProvider, auth_settings: AuthSettings) -> list[Route]:
    """The SDK's OAuth routes plus the owner login page, all storage-guarded."""
    routes = create_auth_routes(
        provider,
        auth_settings.issuer_url,
        client_registration_options=auth_settings.client_registration_options,
        revocation_options=auth_settings.revocation_options,
    )
    # Replace the SDK's /token with one that also enforces RFC 8707 on the
    # presented `resource` before the SDK handler exchanges the code.
    token_endpoint = TokenResourceGuard(provider).handle
    routes = [
        r for r in routes if r.path != TOKEN_PATH
    ] + [
        Route(
            TOKEN_PATH,
            endpoint=cors_middleware(token_endpoint, ["POST", "OPTIONS"]),
            methods=["POST", "OPTIONS"],
        )
    ]

    resource_url = auth_settings.resource_server_url or auth_settings.issuer_url
    registration = auth_settings.client_registration_options
    scopes = registration.valid_scopes if registration is not None else None
    routes += create_protected_resource_routes(
        resource_url=resource_url,
        authorization_servers=[auth_settings.issuer_url],
        scopes_supported=scopes,
        resource_name="Synatyx Context Engine",
    )
    # RFC 9728 §3.1 path insertion: a client whose resource is <base>/mcp looks
    # for /.well-known/oauth-protected-resource/mcp before falling back to the
    # bare path above. Serve both so discovery works either way.
    mcp_resource = AnyHttpUrl(f"{str(resource_url).rstrip('/')}/mcp")
    routes += create_protected_resource_routes(
        resource_url=mcp_resource,
        authorization_servers=[auth_settings.issuer_url],
        scopes_supported=scopes,
        resource_name="Synatyx Context Engine",
    )

    routes.append(
        Route(LOGIN_PATH, endpoint=OwnerLoginHandler(provider).handle, methods=["GET", "POST"])
    )
    # Every OAuth route touches Redis/Postgres; an outage must answer 503, not
    # a traceback (the SDK handlers only catch their own error types).
    return [
        Route(r.path, endpoint=StorageGuard(r.app), methods=sorted(r.methods or []))
        for r in routes
    ]


class StorageGuard:
    """ASGI wrapper: an exception before the response started becomes a 503."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, guarded_send)
        except Exception:
            if started:  # pragma: no cover - cannot replace a response in flight
                raise
            logger.exception("OAuth endpoint %s failed (storage?)", scope.get("path"))
            response = JSONResponse(
                {"error": "temporarily_unavailable", "error_description": STORAGE_UNAVAILABLE},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)


class TokenResourceGuard:
    """RFC 8707 on /token: a presented `resource` must match the code's grant.

    The SDK's TokenHandler parses `resource` but never compares it, so this
    runs first and delegates to the SDK handler on success. `request.form()`
    is cached on the request, so the body is parsed once.
    """

    def __init__(self, provider: SynatyxOAuthProvider) -> None:
        self.provider = provider
        self._sdk = TokenHandler(provider, ClientAuthenticator(provider))

    async def handle(self, request: Request) -> Response:
        form = await request.form()
        presented = form.get("resource")
        code = form.get("code")
        client_id = form.get("client_id")
        if (
            form.get("grant_type") == "authorization_code"
            and isinstance(presented, str) and presented
            and isinstance(code, str) and isinstance(client_id, str)
        ):
            client = await self.provider.get_client(client_id)
            record = (
                await self.provider.load_authorization_code(client, code) if client else None
            )
            if record is not None and not self.provider.resource_matches_grant(
                record.resource, presented
            ):
                logger.warning(
                    "OAuth /token resource mismatch for client %s: granted %s, presented %s",
                    client_id, record.resource, presented,
                )
                return JSONResponse(
                    {
                        "error": "invalid_target",
                        "error_description": "resource does not match the authorization grant",
                    },
                    status_code=400,
                    headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
                )
        response: Response = await self._sdk.handle(request)
        return response


class OwnerLoginHandler:
    """Single-owner consent screen.

    There are no user accounts in Synatyx, so authorization means proving
    ownership of the instance: the owner types AUTH_ADMIN_KEY (or
    OAUTH_OWNER_PASSWORD, when set) and a code is issued for the parked
    /authorize request. The page names the requesting client and the origin
    the code will be sent to, so a phished link is recognisable.
    """

    def __init__(self, provider: SynatyxOAuthProvider) -> None:
        self.provider = provider

    async def handle(self, request: Request) -> Response:
        try:
            return await self._handle(request)
        except Exception:
            logger.exception("Owner login failed to reach storage")
            return _login_page("", None, error=STORAGE_UNAVAILABLE, status_code=503)

    async def _handle(self, request: Request) -> Response:
        if request.method == "GET":
            request_id = request.query_params.get("rid", "")
            if not request_id:
                return _login_page(
                    "", None, error="Missing authorization request.", status_code=400
                )
            pending = await self.provider.get_pending(request_id)
            if pending is None:
                return _login_page(
                    "", None,
                    error="This authorization request expired. Start again from your client.",
                    status_code=400,
                )
            return _login_page(request_id, pending)

        form = await request.form()
        request_id = str(form.get("rid") or "")
        secret = str(form.get("secret") or "")
        client_ip = request.client.host if request.client else None
        try:
            redirect_url = await self.provider.complete_authorization(
                request_id, secret, client_ip=client_ip
            )
        except OAuthLoginError as exc:
            pending = await self.provider.get_pending(request_id) if exc.retryable else None
            return _login_page(
                request_id if pending is not None else "",
                pending,
                error=exc.message,
                status_code=exc.status_code,
            )
        return RedirectResponse(
            redirect_url, status_code=302, headers={"Cache-Control": "no-store"}
        )


_LOGIN_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorize Synatyx</title>
<style>
 body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #0f1115; color: #e6e6e6; display: flex; min-height: 100vh;
        align-items: center; justify-content: center; margin: 0; }}
 form {{ background: #181b21; padding: 2rem; border-radius: 12px; width: 320px;
         box-shadow: 0 10px 30px rgba(0,0,0,.4); }}
 h1 {{ font-size: 1.1rem; margin: 0 0 .25rem; }}
 p {{ font-size: .82rem; color: #9aa0aa; margin: 0 0 1.25rem; line-height: 1.4; }}
 .client {{ color: #e6e6e6; font-weight: 600; }}
 .origin {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: #c9d1d9; }}
 input {{ width: 100%; padding: .6rem .7rem; border-radius: 8px; border: 1px solid #2a2f39;
          background: #0f1115; color: #e6e6e6; box-sizing: border-box; font-size: .95rem; }}
 button {{ width: 100%; margin-top: .9rem; padding: .6rem; border: 0; border-radius: 8px;
           background: #5b8def; color: #fff; font-size: .95rem; cursor: pointer; }}
 .error {{ color: #ff8080; font-size: .82rem; margin: 0 0 .9rem; }}
</style>
</head>
<body>
<form method="post" action="{action}">
  <h1>Authorize access to Synatyx</h1>
  {who}
  {error}
  <input type="hidden" name="rid" value="{rid}">
  <input type="password" name="secret" placeholder="Admin key" autofocus
         autocomplete="current-password" {disabled}>
  <button type="submit" {disabled}>{submit}</button>
</form>
</body>
</html>
"""

_WHO_TEMPLATE = """<p><span class="client">{client}</span> is asking to read and write your
     memories. After you authorize, the browser is sent to
     <span class="origin">{origin}</span>. If you did not start this from that
     client, close this page.</p>"""


def _login_page(
    request_id: str,
    pending: PendingAuthorization | None,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    if pending is not None:
        client = html.escape(pending.client_name or pending.client_id)
        who = _WHO_TEMPLATE.format(client=client, origin=html.escape(pending.redirect_origin))
        submit = f"Authorize {client}"
    else:
        who = "<p>This memory server has a single owner.</p>"
        submit = "Authorize"
    return HTMLResponse(
        _LOGIN_TEMPLATE.format(
            action=LOGIN_PATH,
            who=who,
            rid=html.escape(request_id, quote=True),
            error=f'<p class="error">{html.escape(error)}</p>' if error else "",
            disabled="" if request_id else "disabled",
            submit=submit,
        ),
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )
