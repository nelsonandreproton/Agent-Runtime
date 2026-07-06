"""Entrypoint for the local admin UI: Agents, MCP Servers, and Logs tabs.

Deliberately a SEPARATE process from agent_runtime.cli's A2A gateway. The
gateway is intentionally public (reachable via an ngrok/Cloudflare tunnel so
OutSystems ODC can call it) and has no authentication of its own — the UI
lets an operator edit agents/*.md, edit config/mcp_servers.json (which can
hold live plaintext secrets, e.g. an ACCESSKEY), and browse the full
unredacted content of every logged A2A request/response/tool call. None of
that is safe to expose on the same tunneled port, so this process binds to
127.0.0.1 ONLY, hardcoded, not read from an env var that the gateway also
reads (AGENT_RUNTIME_HOST) — a copy-pasted "just reuse the same host var"
change is exactly the mistake this hardcoding prevents. Never point a tunnel
at this process's port.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from .config import Settings
from .loader import load_all_agents
from .observability import LogStore
from .ui.app import build_ui_app

logging.basicConfig(
    level=os.environ.get("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

UI_HOST = "127.0.0.1"


def main() -> None:
    settings = Settings.from_env()
    port = int(os.environ.get("AGENT_RUNTIME_UI_PORT", "9001"))

    settings.agents_dir.mkdir(parents=True, exist_ok=True)
    agent_names = sorted(load_all_agents(settings.agents_dir).keys())

    mcp_config_path = settings.mcp_config_path or (Path(__file__).resolve().parent.parent / "config" / "mcp_servers.json")

    log_store = None
    if settings.observability_db_path is not None:
        log_store = LogStore(settings.observability_db_path)
        logger.info("Reading agent activity from %s", settings.observability_db_path)
    else:
        logger.warning("AGENT_RUNTIME_OBSERVABILITY_DB is disabled: the Logs tab will show no data")

    app = build_ui_app(
        agents_dir=settings.agents_dir,
        mcp_config_path=mcp_config_path,
        log_store=log_store,
        agent_names=agent_names,
    )

    logger.warning(
        "Serving the local admin UI at http://%s:%d/ - this must stay on localhost, "
        "never behind a public tunnel (it can display secrets and full logged content)",
        UI_HOST,
        port,
    )
    try:
        uvicorn.run(app, host=UI_HOST, port=port, log_level="info")
    finally:
        if log_store is not None:
            log_store.close()


if __name__ == "__main__":
    main()
