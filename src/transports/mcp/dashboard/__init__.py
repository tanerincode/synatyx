"""Admin dashboard: static page plus the /dashboard/api/* JSON routes.

Each module owns one area. A new area adds its routes to DASHBOARD_ROUTES here
and nowhere else.
"""

from __future__ import annotations

from starlette.routing import Route

from src.transports.mcp.dashboard.briefs import (
    api_brief_create,
    api_brief_detail,
    api_brief_update,
    api_briefs,
)
from src.transports.mcp.dashboard.graph import api_graph
from src.transports.mcp.dashboard.indexes import api_index_chunks, api_index_graph, api_indexes
from src.transports.mcp.dashboard.items import api_items
from src.transports.mcp.dashboard.overview import api_overview
from src.transports.mcp.dashboard.page import dashboard_page
from src.transports.mcp.dashboard.tasks import api_tasks
from src.transports.mcp.dashboard.usage import api_usage
from src.transports.mcp.dashboard.users import api_users

DASHBOARD_ROUTES: list[Route] = [
    Route("/dashboard", dashboard_page),
    Route("/dashboard/api/overview", api_overview),
    Route("/dashboard/api/items", api_items),
    Route("/dashboard/api/tasks", api_tasks),
    Route("/dashboard/api/users", api_users),
    Route("/dashboard/api/graph", api_graph),
    Route("/dashboard/api/indexes", api_indexes),
    Route("/dashboard/api/index_graph", api_index_graph),
    Route("/dashboard/api/index_chunks", api_index_chunks),
    Route("/dashboard/api/usage", api_usage),
    Route("/dashboard/api/briefs", api_briefs),
    Route("/dashboard/api/briefs", api_brief_create, methods=["POST"]),
    Route("/dashboard/api/briefs/{id}", api_brief_detail),
    Route("/dashboard/api/briefs/{id}", api_brief_update, methods=["PATCH"]),
]

__all__ = [
    "DASHBOARD_ROUTES",
    "api_brief_create",
    "api_brief_detail",
    "api_brief_update",
    "api_briefs",
    "api_graph",
    "api_index_chunks",
    "api_index_graph",
    "api_indexes",
    "api_items",
    "api_overview",
    "api_tasks",
    "api_usage",
    "api_users",
    "dashboard_page",
]
