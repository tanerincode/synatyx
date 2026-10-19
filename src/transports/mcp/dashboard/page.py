"""The dashboard web app at /dashboard.

When the Vite build exists (web/dist, built in the Dockerfile's node stage) it
is served as a single-page app: index.html for /dashboard and every client
route under it, and the content-hashed files in web/dist/assets with a
long-lived cache header. Without a build (Python-only dev) /dashboard falls
back to the legacy single-file dashboard.html and other paths are 404.

These responses are public (see AdminKeyAuthMiddleware._is_public): they hold
no data. Everything the app shows comes from /dashboard/api/*, which stays
behind the admin key, and the catch-all below never serves anything under api.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Match

_LEGACY_HTML_PATH = Path(__file__).parent.parent / "dashboard.html"
# <repo>/web/dist (in the image: /app/web/dist). Module-level so tests can point
# it at a temporary build.
_DIST_DIR = Path(__file__).resolve().parents[4] / "web" / "dist"

# index.html must be revalidated so a deploy is picked up at once; assets are
# content-hashed by Vite, so a given URL never changes.
_INDEX_CACHE = "no-cache"
_ASSET_CACHE = "public, max-age=31536000, immutable"


def _index_file() -> Path | None:
    index = _DIST_DIR / "index.html"
    return index if index.is_file() else None


def _index_response(index: Path) -> FileResponse:
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": _INDEX_CACHE})


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not found"}, status_code=404)


def _other_route_allows_another_method(request: Request) -> bool:
    """True when an API route matches this path but not this method. The
    catch-all is a full match for GET, so without this check Starlette would
    route GET /dashboard/api/items/{id}/pin here instead of answering 405."""
    for route in request.app.router.routes:
        if getattr(route, "endpoint", None) is dashboard_spa:
            continue
        match, _ = route.matches(request.scope)
        if match == Match.PARTIAL:
            return True
    return False


async def dashboard_page(_request: Request) -> Response:
    """/dashboard: the built app when present, else the legacy single file."""
    index = _index_file()
    if index is not None:
        return _index_response(index)
    return HTMLResponse(_LEGACY_HTML_PATH.read_text(encoding="utf-8"))


async def dashboard_spa(request: Request) -> Response:
    """/dashboard/{path}: a file under web/dist/assets, or index.html for a
    client-side route. Mounted after every /dashboard/api route."""
    rel = str(request.path_params.get("path") or "")
    # Empty and "." segments are dropped, ".." is refused outright: client
    # routes never contain either.
    parts = [p for p in rel.split("/") if p and p != "."]
    if ".." in parts:
        return _not_found()
    # Never answer for the API namespace, however the path is spelled
    # (/dashboard//api/x and /dashboard/./api/x arrive here as "/api/x", "./api/x").
    if parts and parts[0] == "api":
        if _other_route_allows_another_method(request):
            return JSONResponse({"error": "method not allowed"}, status_code=405)
        return _not_found()

    index = _index_file()
    if index is None:
        return _not_found()

    if parts and parts[0] == "assets":
        assets = (_DIST_DIR / "assets").resolve()
        target = _DIST_DIR.joinpath(*parts).resolve()
        # resolve() collapses ".." and symlinks; the result must stay inside
        # web/dist/assets and be a real file.
        if not target.is_relative_to(assets) or not target.is_file():
            return _not_found()
        return FileResponse(target, headers={"Cache-Control": _ASSET_CACHE})

    return _index_response(index)
