"""Assembles the local admin UI: Agents, MCP Servers, and Logs tabs.

This is a SEPARATE app from the A2A gateway (agent_runtime/a2a_gateway.py) —
see agent_runtime/ui_cli.py for why it must never be mounted on, or served
alongside, the gateway that's tunneled to external callers like OutSystems
ODC. This module only assembles routes; it has no opinion on host/port
binding.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import PlainTextResponse, RedirectResponse

from agent_runtime.observability import LogStore

from .agents_routes import build_agents_router
from .logs_routes import build_logs_router
from .mcp_routes import build_mcp_router

_UI_DIR = Path(__file__).resolve().parent

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def build_ui_app(
    agents_dir: Path,
    mcp_config_path: Path,
    log_store: LogStore | None,
    agent_names: list[str],
) -> FastAPI:
    """Builds the admin UI's FastAPI app.

    `agent_names` is the list of agents the currently-running gateway process
    has loaded (used only to populate the Logs tab's filter dropdown) — it is
    read once at UI startup, same as everything else here; the UI reflects
    files on disk directly and always requires a gateway restart to pick up
    Agents/MCP-servers changes (see README's "Local UI" section).
    """
    app = FastAPI(title="Agent Runtime UI")
    templates = Jinja2Templates(directory=str(_UI_DIR / "templates"))

    @app.middleware("http")
    async def reject_cross_origin_mutations(request: Request, call_next):
        # This app has no auth and no CSRF tokens — it relies entirely on
        # being unreachable except from 127.0.0.1 (see ui_cli.py). But a
        # browser will still deliver a same-origin-rule-exempt simple POST
        # from ANY page open on the operator's machine to this port. Origin
        # is a browser-set, unspoofable-by-page-JS header, so rejecting a
        # mismatched Origin on state-changing requests closes that drive-by
        # vector cheaply, without needing session/token infrastructure.
        if request.method in _MUTATING_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and urlparse(origin).netloc != request.url.netloc:
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(_UI_DIR / "static")), name="static")
    app.include_router(build_agents_router(agents_dir, templates))
    app.include_router(build_mcp_router(mcp_config_path, templates))
    app.include_router(build_logs_router(log_store, agent_names, templates))

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/ui/agents")

    return app
