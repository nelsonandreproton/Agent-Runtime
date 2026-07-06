import asyncio
import contextlib

import anyio
import pytest

from agent_runtime.config import MCPServerConfig
from agent_runtime.mcp_client import (
    MCPServerConnection,
    MCPToolCallTimeout,
    MCPToolsClient,
    ResolvedTool,
    _build_subprocess_env,
)


def test_subprocess_env_does_not_inherit_a_secret_from_the_parent_process(monkeypatch):
    monkeypatch.setenv("SOME_SECRET_API_KEY", "sk-should-not-leak")

    env = _build_subprocess_env(server_env={})

    assert "SOME_SECRET_API_KEY" not in env


def test_subprocess_env_forwards_proxy_vars_from_the_parent_process(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8080")
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    env = _build_subprocess_env(server_env={})

    assert env["HTTPS_PROXY"] == "http://proxy.internal:8080"
    assert "HTTP_PROXY" not in env


def test_subprocess_env_includes_the_servers_own_configured_env(monkeypatch):
    monkeypatch.delenv("SERVER_API_KEY", raising=False)

    env = _build_subprocess_env(server_env={"SERVER_API_KEY": "server-specific-value"})

    assert env["SERVER_API_KEY"] == "server-specific-value"


def test_subprocess_env_server_config_overrides_a_same_named_proxy_var(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://from-parent-process:8080")

    env = _build_subprocess_env(server_env={"HTTPS_PROXY": "http://from-server-config:8080"})

    assert env["HTTPS_PROXY"] == "http://from-server-config:8080"


class HangingSession:
    """Simulates an MCP server that never responds to call_tool."""

    async def call_tool(self, name, arguments):
        await asyncio.sleep(10)
        raise AssertionError("should have timed out before this returned")


@pytest.mark.asyncio
async def test_call_tool_times_out_instead_of_hanging_forever():
    config = MCPServerConfig(name="slow-server", command="irrelevant")
    conn = MCPServerConnection(config, call_timeout=0.05)
    conn.session = HangingSession()

    with pytest.raises(MCPToolCallTimeout, match="slow-server"):
        await conn.call_tool("some_tool", {})


@pytest.mark.asyncio
async def test_tools_client_surfaces_a_timeout_as_a_tool_error_not_an_exception():
    config = MCPServerConfig(name="slow-server", command="irrelevant")
    client = MCPToolsClient([config], call_timeout=0.05)
    conn = MCPServerConnection(config, call_timeout=0.05)
    conn.session = HangingSession()
    client._connections["slow-server"] = conn
    client._resolved["Read"] = ResolvedTool(
        server_name="slow-server", mcp_name="read_text_file", description="", input_schema={}
    )

    result_text, is_error = await client.call("Read", {"path": "a.py"})

    assert is_error is True
    assert "slow-server" in result_text


@pytest.mark.asyncio
async def test_a_hung_server_does_not_block_calls_to_a_different_healthy_server():
    slow_config = MCPServerConfig(name="slow-server", command="irrelevant")
    client = MCPToolsClient([slow_config], call_timeout=0.05)

    slow_conn = MCPServerConnection(slow_config, call_timeout=0.05)
    slow_conn.session = HangingSession()
    client._connections["slow-server"] = slow_conn
    client._resolved["Read"] = ResolvedTool(
        server_name="slow-server", mcp_name="read_text_file", description="", input_schema={}
    )

    class FastSession:
        async def call_tool(self, name, arguments):
            class Content:
                type = "text"
                text = "fast result"

            class Result:
                content = [Content()]
                isError = False

            return Result()

    fast_config = MCPServerConfig(name="fast-server", command="irrelevant")
    fast_conn = MCPServerConnection(fast_config, call_timeout=5)
    fast_conn.session = FastSession()
    client._connections["fast-server"] = fast_conn
    client._resolved["Write"] = ResolvedTool(
        server_name="fast-server", mcp_name="write_file", description="", input_schema={}
    )

    slow_result, fast_result = await asyncio.gather(
        client.call("Read", {"path": "a.py"}), client.call("Write", {"path": "b.py"})
    )

    assert slow_result[1] is True
    assert fast_result == ("fast result", False)


@contextlib.asynccontextmanager
async def _anyio_task_group_scope():
    """Stand-in for what mcp's stdio_client() actually does: enter an anyio
    task group (a cancel scope) inside an AsyncExitStack. Reproduces the real
    anyio ordering constraint without spawning a subprocess."""
    async with anyio.create_task_group():
        yield


@pytest.mark.asyncio
async def test_close_all_closes_multiple_real_connections_without_an_anyio_cancel_scope_error():
    """Regression test for a real bug: connect_all() enters each connection's
    stdio transport (an anyio cancel scope) sequentially in one task. anyio
    requires cancel scopes to exit in strict reverse-of-entry order within a
    task — closing forward-order tries to exit the first (outermost) scope
    while a later connection's (inner) scope is still open, and anyio raises
    "cancel scope that isn't the current task's current cancel scope". This
    only surfaces with 2+ real (non-stubbed) connections open at once, which
    the fully-stubbed tests elsewhere in this file can't catch — verified
    exposed against a live two-MCP-server config (filesystem + swarmmcp)
    before the fix in close_all() (reversed iteration order)."""
    config_a = MCPServerConfig(name="server-a", command="irrelevant")
    config_b = MCPServerConfig(name="server-b", command="irrelevant")
    conn_a = MCPServerConnection(config_a)
    conn_b = MCPServerConnection(config_b)

    # Enter real anyio cancel scopes sequentially, same task, same order
    # connect_all() would (via conn.connect() -> exit_stack.enter_async_context).
    await conn_a._exit_stack.enter_async_context(_anyio_task_group_scope())
    await conn_b._exit_stack.enter_async_context(_anyio_task_group_scope())

    client = MCPToolsClient([config_a, config_b])
    client._connections = {"server-a": conn_a, "server-b": conn_b}

    await client.close_all()  # must not raise
