from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse

_HTML_PATH = Path(__file__).parent.parent / "dashboard.html"


async def dashboard_page(_request: Request) -> HTMLResponse:
    """Static dashboard shell. Public — all data comes from the authed API."""
    return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))
