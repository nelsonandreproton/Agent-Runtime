"""Runtime configuration, loaded from environment variables and a JSON file for MCP servers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # Claude Code tool name -> concrete tool name exposed by this MCP server,
    # e.g. {"Read": "read_text_file", "Write": "write_file"}.
    tool_aliases: dict[str, str] = field(default_factory=dict)


def load_mcp_servers(path: Path | None) -> list[MCPServerConfig]:
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = []
    for entry in data.get("servers", []):
        servers.append(
            MCPServerConfig(
                name=entry["name"],
                command=entry["command"],
                args=entry.get("args", []),
                env=entry.get("env", {}),
                tool_aliases=entry.get("tool_aliases", {}),
            )
        )
    return servers


@dataclass(frozen=True)
class Settings:
    agents_dir: Path
    agent_name: str
    llama_base_url: str
    llama_model: str
    mcp_config_path: Path | None
    gateway_host: str
    gateway_port: int
    public_url: str
    request_timeout: float

    @classmethod
    def from_env(cls) -> Settings:
        host = os.environ.get("AGENT_RUNTIME_HOST", "0.0.0.0")
        port = int(os.environ.get("AGENT_RUNTIME_PORT", "9000"))
        mcp_config = os.environ.get("AGENT_RUNTIME_MCP_CONFIG")
        return cls(
            agents_dir=Path(os.environ.get("AGENT_RUNTIME_AGENTS_DIR", "agents")),
            agent_name=os.environ.get("AGENT_RUNTIME_AGENT_NAME", "code-reviewer"),
            llama_base_url=os.environ.get("AGENT_RUNTIME_LLAMA_BASE_URL", "http://127.0.0.1:8080"),
            llama_model=os.environ.get("AGENT_RUNTIME_LLAMA_MODEL", "local-model"),
            mcp_config_path=Path(mcp_config) if mcp_config else None,
            gateway_host=host,
            gateway_port=port,
            public_url=os.environ.get("AGENT_RUNTIME_PUBLIC_URL", f"http://{host}:{port}/"),
            request_timeout=float(os.environ.get("AGENT_RUNTIME_TIMEOUT", "120")),
        )
