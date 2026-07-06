import asyncio

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
