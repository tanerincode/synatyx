"""Serving the dashboard web app at /dashboard.

The app shell, its client routes and its hashed assets are public; the data
API under /dashboard/api stays behind the admin key. These tests run the real
DASHBOARD_ROUTES behind the real AdminKeyAuthMiddleware against a fake build
of web/dist, and send hostile paths as raw ASGI scopes so no HTTP client
normalises them first.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.transports.mcp.dashboard import DASHBOARD_ROUTES, page
from src.transports.mcp.http_server import AdminKeyAuthMiddleware
from tests.test_dashboard import FakePostgres, FakeQdrant, _item

ADMIN_KEY = "admin-key-123"
KEY = {"Authorization": f"Bearer {ADMIN_KEY}"}
INDEX_HTML = "<!doctype html><title>Synatyx — Memory Core</title><div id=root></div>"
ASSET = "index-AbC123.js"


async def _health(_request: Any) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _app() -> Starlette:
    app = Starlette(
        routes=[Route("/health", _health), *DASHBOARD_ROUTES],
        middleware=[
            Middleware(
                AdminKeyAuthMiddleware,
                admin_key=ADMIN_KEY,
                header_name="X-Auth-Key",
                public_paths=frozenset({"/health", "/dashboard"}),
            )
        ],
    )
    app.state.qdrant = FakeQdrant({"ctx_synatyx": [_item()]})
    app.state.postgres = FakePostgres([], None, None)
    return app


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake Vite build, plus a secret file next to it that must stay unreachable."""
    d = tmp_path / "web" / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (d / "assets" / ASSET).write_text("console.log('app')", encoding="utf-8")
    (tmp_path / "web" / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    (tmp_path / "config.py").write_text("TOP-SECRET", encoding="utf-8")
    monkeypatch.setattr(page, "_DIST_DIR", d)
    return d


@pytest.fixture
def no_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page, "_DIST_DIR", tmp_path / "missing" / "dist")


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def _raw(
    path: str, *, method: str = "GET", headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    """Call the app with an un-normalised, already-decoded path, as a server
    would hand it over."""
    import asyncio

    path, _, query = path.partition("?")
    status = 0
    out_headers: dict[str, str] = {}
    body = b""
    sent_request = False
    done: asyncio.Event | None = None

    async def receive() -> dict[str, Any]:
        # One request message, then (like a real server) a disconnect only once
        # the response is complete — FileResponse listens for it.
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        assert done is not None
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status, out_headers, body
        if message["type"] == "http.response.start":
            status = message["status"]
            out_headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")
            if not message.get("more_body") and done is not None:
                done.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "state": {},
    }
    async def run() -> None:
        nonlocal done
        done = asyncio.Event()
        await asyncio.wait_for(_app()(scope, receive, send), timeout=5)

    asyncio.run(run())
    return status, out_headers, body


# ── the API stays private ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/api/items",
        "/dashboard/api",
        "/dashboard/api/",
        "/dashboard/api/unknown",
        "/dashboard/apix",
    ],
)
def test_api_namespace_needs_the_key(client: TestClient, dist: Path, path: str) -> None:
    assert client.get(path).status_code == 401


def test_api_with_key_reaches_handlers_or_json_404(client: TestClient, dist: Path) -> None:
    ok = client.get("/dashboard/api/items?collection=ctx_synatyx", headers=KEY)
    assert ok.status_code == 200
    assert ok.json()["items"][0]["content"] == "Qdrant runs on port 6333"
    for path in ("/dashboard/api", "/dashboard/api/", "/dashboard/api/unknown"):
        res = client.get(path, headers=KEY)
        assert res.status_code == 404, path
        assert res.headers["content-type"].startswith("application/json"), path
        assert INDEX_HTML not in res.text
    # a POST-only API route still answers 405 to GET, not the catch-all's 404
    wrong_method = client.get("/dashboard/api/items/x/pin", headers=KEY)
    assert wrong_method.status_code == 405
    assert client.get("/dashboard/api/items/x/pin").status_code == 401


@pytest.mark.parametrize(
    "path", ["/dashboard//api/items", "/dashboard///api/items", "/dashboard/./api/items"]
)
def test_respelled_api_paths_never_return_data(dist: Path, path: str) -> None:
    status, _, body = _raw(path + "?collection=ctx_synatyx")
    assert status in (401, 404), path
    assert b"Qdrant runs on port 6333" not in body


def test_percent_encoded_api_is_decoded_before_the_check(dist: Path) -> None:
    # A server decodes %61 to "a" before the app sees scope["path"].
    status, _, body = _raw("/dashboard/api/items")
    assert status == 401
    assert b"Qdrant" not in body
    # httpx sends the encoded form; Starlette's TestClient decodes it like a server would.
    res = TestClient(_app()).get("/dashboard/%61pi/items?collection=ctx_synatyx")
    assert res.status_code == 401


# ── traversal never leaves web/dist ─────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/../health",
        "/dashboard/assets/../../secret.txt",
        "/dashboard/assets/../../../config.py",
        "/dashboard/assets/../index.html",
        "/dashboard/assets/%2e%2e/%2e%2e/secret.txt",
        "/dashboard/../../config.py",
    ],
)
def test_traversal_gives_404_or_the_shell(dist: Path, path: str) -> None:
    status, headers, body = _raw(path)
    assert b"TOP-SECRET" not in body, path
    assert status == 404 or body == INDEX_HTML.encode(), (path, status)


def test_symlinked_asset_out_of_dist_is_refused(dist: Path) -> None:
    (dist / "assets" / "leak.js").symlink_to(dist.parent / "secret.txt")
    status, _, body = _raw("/dashboard/assets/leak.js")
    assert status == 404
    assert b"TOP-SECRET" not in body


def test_missing_asset_is_404_not_the_shell(client: TestClient, dist: Path) -> None:
    res = client.get("/dashboard/assets/nope.js")
    assert res.status_code == 404
    assert INDEX_HTML not in res.text


# ── the app itself ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/dashboard/",
        "/dashboard/briefs",
        "/dashboard/briefs/x",
        "/dashboard/memories?project=synatyx&item=abc",
    ],
)
def test_client_routes_get_the_shell_without_a_key(
    client: TestClient, dist: Path, path: str
) -> None:
    res = client.get(path)
    assert res.status_code == 200, path
    assert res.text == INDEX_HTML
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_public_and_immutable(client: TestClient, dist: Path) -> None:
    res = client.get(f"/dashboard/assets/{ASSET}")
    assert res.status_code == 200
    assert res.text == "console.log('app')"
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in res.headers["content-type"]


def test_head_is_allowed_and_writes_are_not(client: TestClient, dist: Path) -> None:
    assert client.head("/dashboard/briefs/x").status_code == 200
    assert client.post("/dashboard/briefs").status_code == 405
    assert client.put("/dashboard/assets/" + ASSET).status_code == 405
    # POSTs to the API still go through auth first
    assert client.post("/dashboard/api/briefs", json={}).status_code == 401


# ── Python-only dev: no build ───────────────────────────────────────────────


def test_without_a_build_dashboard_falls_back_to_the_legacy_page(
    client: TestClient, no_dist: None
) -> None:
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert "Synatyx" in res.text
    assert res.headers["content-type"].startswith("text/html")
    assert client.get("/dashboard/briefs").status_code == 404
    assert client.get(f"/dashboard/assets/{ASSET}").status_code == 404


def test_other_public_paths_unchanged(client: TestClient, dist: Path) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/healthz").status_code == 401  # unknown non-dashboard paths still need a key
