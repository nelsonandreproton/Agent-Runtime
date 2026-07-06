"""MCP Client: connects to configured MCP servers and exposes their tools.

Each connected server's tools are exposed under the Claude Code tool aliases
declared in its config entry (tool_aliases), so the Agent Runtime can offer
agents an OpenAI-style tool schema keyed by the names their markdown
frontmatter already uses (Read, Write, Edit, Glob, ...).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from .config import MCPServerConfig

logger = logging.getLogger(__name__)

# The MCP SDK spawns server subprocesses with a deliberately minimal
# environment (HOME/PATH/...) so third-party MCP servers don't inherit this
# process's secrets. Proxy settings are added back explicitly, since package
# runners like npx/uvx need them to reach their registries from behind a
# corporate proxy — this is the one class of env var that's both commonly
# required and safe to forward.
_PASSTHROUGH_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


def _build_subprocess_env(server_env: dict[str, str]) -> dict[str, str]:
    """Builds the environment an MCP server subprocess is spawned with.

    Starts from the MCP SDK's minimal default env (HOME/PATH/...) rather than
    this process's full os.environ, so a third-party MCP server never inherits
    secrets (API keys, tokens) this process happens to have. Proxy variables
    are the one explicit exception — package runners like npx/uvx need them
    to reach their registries from behind a corporate proxy — and the
    server's own config.env (e.g. an API key it specifically needs) is
    applied last so it can override either.
    """
    env = get_default_environment()
    for key in _PASSTHROUGH_ENV_VARS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    env.update(server_env)
    return env


@dataclass(frozen=True)
class ResolvedTool:
    server_name: str
    mcp_name: str
    description: str
    input_schema: dict[str, Any]


class MCPToolCallTimeout(RuntimeError):
    """Raised when an MCP server doesn't respond to a tool call within the configured timeout."""


class MCPServerConnection:
    def __init__(self, config: MCPServerConfig, call_timeout: float = 60.0):
        self.config = config
        self._call_timeout = call_timeout
        self._exit_stack = contextlib.AsyncExitStack()
        self.session: ClientSession | None = None

    async def connect(self) -> None:
        env = _build_subprocess_env(self.config.env)
        params = StdioServerParameters(command=self.config.command, args=self.config.args, env=env)
        read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        logger.info("Connected to MCP server '%s' (%s)", self.config.name, self.config.command)

    async def close(self) -> None:
        await self._exit_stack.aclose()

    async def list_tools(self):
        assert self.session is not None
        result = await self.session.list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        assert self.session is not None
        try:
            result = await asyncio.wait_for(self.session.call_tool(name, arguments), timeout=self._call_timeout)
        except asyncio.TimeoutError as exc:
            # A hung/slow MCP server must not stall this request indefinitely —
            # the connection pool is shared across every agent, so an unbounded
            # await here degrades the whole gateway, not just one caller.
            raise MCPToolCallTimeout(
                f"MCP server '{self.config.name}' did not respond to tool '{name}' "
                f"within {self._call_timeout}s"
            ) from exc
        text_parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
        if text_parts:
            text = "\n".join(text_parts)
        else:
            text = json.dumps([c.model_dump(mode="json") for c in result.content])
        return text, bool(result.isError)


class MCPToolsClient:
    """Aggregates one or more MCP servers behind a single Claude-Code-tool-alias namespace."""

    def __init__(self, servers: list[MCPServerConfig], call_timeout: float = 60.0):
        self._configs = servers
        self._call_timeout = call_timeout
        self._connections: dict[str, MCPServerConnection] = {}
        self._resolved: dict[str, ResolvedTool] = {}

    async def connect_all(self) -> None:
        for config in self._configs:
            conn = MCPServerConnection(config, call_timeout=self._call_timeout)
            await conn.connect()
            self._connections[config.name] = conn

            tools_by_name = {t.name: t for t in await conn.list_tools()}
            for alias, mcp_name in config.tool_aliases.items():
                tool = tools_by_name.get(mcp_name)
                if tool is None:
                    logger.warning(
                        "MCP server '%s' does not expose tool '%s' (aliased as '%s'); skipping",
                        config.name,
                        mcp_name,
                        alias,
                    )
                    continue
                self._resolved[alias] = ResolvedTool(
                    server_name=config.name,
                    mcp_name=mcp_name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                )

    async def close_all(self) -> None:
        # anyio requires cancel scopes to exit in strict reverse-of-entry
        # order within a task. connect_all() enters each connection's stdio
        # transport sequentially in this same task, so closing in forward
        # order tries to exit the first (outermost) scope while a later
        # connection's (inner) scopes are still open — anyio raises
        # "cancel scope that isn't the current task's current cancel scope".
        # Closing last-connected-first restores correct LIFO order.
        for conn in reversed(list(self._connections.values())):
            await conn.close()

    def known_aliases(self) -> list[str]:
        return list(self._resolved)

    def openai_tools_schema(self, aliases: list[str]) -> list[dict[str, Any]]:
        schema = []
        for alias in aliases:
            resolved = self._resolved.get(alias)
            if resolved is None:
                continue
            schema.append(
                {
                    "type": "function",
                    "function": {
                        "name": alias,
                        "description": resolved.description,
                        "parameters": resolved.input_schema,
                    },
                }
            )
        return schema

    async def call(self, alias: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        resolved = self._resolved.get(alias)
        if resolved is None:
            return f"Tool '{alias}' is not available in this runtime.", True
        conn = self._connections[resolved.server_name]
        try:
            return await conn.call_tool(resolved.mcp_name, arguments)
        except MCPToolCallTimeout as exc:
            # Surfaced to the model as a tool error (same shape as any other
            # tool failure) rather than propagating and failing the whole task —
            # a timeout on one call shouldn't prevent the model from trying
            # something else or giving a partial answer.
            logger.warning("Tool '%s' timed out: %s", alias, exc)
            return str(exc), True
