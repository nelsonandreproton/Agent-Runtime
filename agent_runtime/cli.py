"""Entrypoint: loads markdown agents, connects their MCP tools, and serves them over A2A."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .a2a_gateway import build_gateway_app
from .config import Settings, load_mcp_servers
from .llm_client import LlamaServerClient
from .loader import load_all_agents
from .mcp_client import MCPToolsClient
from .runtime import AgentRuntime
from .task_store import SQLiteTaskStore

# AGENT_RUNTIME_LOG_LEVEL defaults to INFO, which logs request/response sizes
# and outcomes but never raw content. Set to DEBUG to additionally log full
# request/response/tool-call bodies (agent input, LLM output, tool arguments
# and results) — this can include sensitive data (file contents an agent
# read, CRM records, transcripts) and should only be enabled for local
# debugging, never left on in a deployment whose logs are captured off-box.
logging.basicConfig(
    level=os.environ.get("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings.from_env()

    all_agents = load_all_agents(settings.agents_dir)
    if settings.agent_names is not None:
        missing = set(settings.agent_names) - all_agents.keys()
        if missing:
            raise SystemExit(f"AGENT_RUNTIME_AGENT_NAMES references unknown agent(s): {sorted(missing)}")
        agents = [all_agents[name] for name in settings.agent_names]
    else:
        agents = list(all_agents.values())
    if not agents:
        raise SystemExit(f"No agent .md files found in {settings.agents_dir}")
    logger.info("Loaded agents: %s", [a.name for a in agents])

    settings.working_dir.mkdir(parents=True, exist_ok=True)
    mcp_client = MCPToolsClient(
        load_mcp_servers(settings.mcp_config_path, working_dir=settings.working_dir),
        call_timeout=settings.mcp_call_timeout,
    )
    await mcp_client.connect_all()
    logger.info("MCP tools available: %s", mcp_client.known_aliases() or "(none)")

    llm_client = LlamaServerClient(
        base_url=settings.llama_base_url,
        model=settings.llama_model,
        timeout=settings.request_timeout,
    )
    agent_runtime = AgentRuntime(llm_client, mcp_client)

    task_store = None
    if settings.task_store_path is not None:
        task_store = SQLiteTaskStore(settings.task_store_path)
        logger.info("Persisting A2A task state to %s", settings.task_store_path)
    else:
        logger.warning("AGENT_RUNTIME_TASK_STORE_PATH unset: task state is in-memory only, lost on restart")

    app = build_gateway_app(agents, agent_runtime, settings.public_url, task_store=task_store)
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.gateway_host, port=settings.gateway_port, log_level="info")
    )

    logger.info("Serving %d agent(s) over A2A at %s (see GET /agents)", len(agents), settings.public_url)
    try:
        await server.serve()
    finally:
        # Each cleanup gets its own finally so one raising doesn't skip the other.
        try:
            await mcp_client.close_all()
        finally:
            if task_store is not None:
                task_store.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
