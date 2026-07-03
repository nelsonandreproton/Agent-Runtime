"""Entrypoint: loads markdown agents, connects their MCP tools, and serves them over A2A."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from .a2a_gateway import build_gateway_app
from .config import Settings, load_mcp_servers
from .llm_client import LlamaServerClient
from .loader import load_all_agents
from .mcp_client import MCPToolsClient
from .runtime import AgentRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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

    mcp_client = MCPToolsClient(load_mcp_servers(settings.mcp_config_path))
    await mcp_client.connect_all()
    logger.info("MCP tools available: %s", mcp_client.known_aliases() or "(none)")

    llm_client = LlamaServerClient(
        base_url=settings.llama_base_url,
        model=settings.llama_model,
        timeout=settings.request_timeout,
    )
    agent_runtime = AgentRuntime(llm_client, mcp_client)

    app = build_gateway_app(agents, agent_runtime, settings.public_url)
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.gateway_host, port=settings.gateway_port, log_level="info")
    )

    logger.info("Serving %d agent(s) over A2A at %s (see GET /agents)", len(agents), settings.public_url)
    try:
        await server.serve()
    finally:
        await mcp_client.close_all()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
