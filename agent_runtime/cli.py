"""Entrypoint: loads one markdown agent, connects its MCP tools, and serves it over A2A."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from .a2a_gateway import build_app
from .config import Settings, load_mcp_servers
from .llm_client import LlamaServerClient
from .loader import load_agent
from .mcp_client import MCPToolsClient
from .runtime import AgentRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings.from_env()

    agent = load_agent(settings.agents_dir, settings.agent_name)
    logger.info("Loaded agent '%s' from %s", agent.name, agent.source_path)

    mcp_client = MCPToolsClient(load_mcp_servers(settings.mcp_config_path))
    await mcp_client.connect_all()
    logger.info("MCP tools available: %s", mcp_client.known_aliases() or "(none)")

    llm_client = LlamaServerClient(
        base_url=settings.llama_base_url,
        model=settings.llama_model,
        timeout=settings.request_timeout,
    )
    agent_runtime = AgentRuntime(llm_client, mcp_client)

    app = build_app(agent, agent_runtime, settings.public_url)
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.gateway_host, port=settings.gateway_port, log_level="info")
    )

    logger.info("Serving agent '%s' over A2A at %s", agent.name, settings.public_url)
    try:
        await server.serve()
    finally:
        await mcp_client.close_all()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
