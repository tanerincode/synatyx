"""The POSIX sh installer served at GET /index/install.

Kept apart from http_server.py: it is pure string building, so it can be tested
without an ASGI app, and the route only has to call two functions.
"""
from __future__ import annotations

import re
import shlex

from starlette.requests import Request

# Matches the default in settings.public_url. When the configured value is still
# the default there is no real public URL, so the request itself is the source.
DEFAULT_PUBLIC_URL = "http://localhost:9000"

_SAFE_URL = re.compile(r"^https?://[A-Za-z0-9._~:\[\]@%/+-]+$")

_TEMPLATE = """#!/bin/sh
# Synatyx local indexer installer. Served by {url}/index/install.
#   curl -s -H "X-Auth-Key: $KEY" {url}/index/install | SYNATYX_AUTH_KEY=$KEY sh
set -eu

URL={quoted_url}
DEST="${{HOME}}/.local/bin"

if [ -z "${{SYNATYX_AUTH_KEY:-}}" ]; then
    echo "synatyx-index install: SYNATYX_AUTH_KEY is not set." >&2
    echo 'Run: curl -s -H "X-Auth-Key: $KEY" <server>/index/install \\' >&2
    echo '       | SYNATYX_AUTH_KEY=$KEY sh' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "synatyx-index install: python3 is required and was not found on PATH." >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "synatyx-index install: curl is required and was not found on PATH." >&2
    exit 1
fi

mkdir -p "$DEST"
TMP="$(mktemp "$DEST/.synatyx-index.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

if ! curl -fsS -H "X-Auth-Key: $SYNATYX_AUTH_KEY" "$URL/index/script" -o "$TMP"; then
    echo "synatyx-index install: could not download $URL/index/script (wrong key or URL?)." >&2
    exit 1
fi
chmod 755 "$TMP"
mv "$TMP" "$DEST/synatyx-index"

exec python3 "$DEST/synatyx-index" install --url "$URL"
"""


def _first(value: str | None) -> str:
    """First entry of a possibly comma-separated forwarded header."""
    return (value or "").split(",")[0].strip()


def resolve_base_url(request: Request, public_url: str = "") -> str:
    """The URL a client should use to reach this server, without a trailing slash.

    Order: X-Forwarded-Host (with X-Forwarded-Proto, default https) from a proxy
    such as Caddy; then a configured public URL, unless it is the unset default;
    then the URL the request came in on.
    """
    host = _first(request.headers.get("x-forwarded-host"))
    if host:
        proto = _first(request.headers.get("x-forwarded-proto")) or "https"
        candidate = f"{proto}://{host}"
    elif public_url and public_url.rstrip("/") != DEFAULT_PUBLIC_URL:
        candidate = public_url.rstrip("/")
    else:
        candidate = str(request.base_url).rstrip("/")
    if not _SAFE_URL.match(candidate):
        raise ValueError(f"refusing to build an installer for unsafe URL {candidate!r}")
    return candidate


def render_install_script(url: str) -> str:
    """The installer for `url`. The URL is shell-quoted wherever it is assigned."""
    return _TEMPLATE.format(url=url, quoted_url=shlex.quote(url))
