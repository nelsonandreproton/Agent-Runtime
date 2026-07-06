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


# Placeholder an mcp_servers.json entry can use in `args`/`env` values in place
# of a hardcoded filesystem path, e.g. ["mcp-server-filesystem", "{WORKING_DIR}"].
# load_mcp_servers() substitutes it with the runtime's configured working_dir
# and verifies the resulting path is still inside it (guards {WORKING_DIR}/../x
# style escapes) — this is the one enforced sandbox root every filesystem-
# capable MCP server is scoped to, kept separate from the runtime's own source
# and config/ (which holds secrets an agent must never be able to read).
WORKING_DIR_PLACEHOLDER = "{WORKING_DIR}"


class MCPServerConfigError(ValueError):
    """Raised when an mcp_servers.json entry resolves outside the configured working_dir."""


def load_mcp_servers(path: Path | None, working_dir: Path | None = None) -> list[MCPServerConfig]:
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    resolved_working_dir = working_dir.resolve() if working_dir is not None else None
    servers = []
    for entry in data.get("servers", []):
        servers.append(
            MCPServerConfig(
                name=entry["name"],
                command=entry["command"],
                args=[
                    _substitute_working_dir(v, resolved_working_dir, entry["name"]) for v in entry.get("args", [])
                ],
                env={
                    k: _substitute_working_dir(v, resolved_working_dir, entry["name"])
                    for k, v in entry.get("env", {}).items()
                },
                tool_aliases=entry.get("tool_aliases", {}),
            )
        )
    return servers


def _substitute_working_dir(value: str, working_dir: Path | None, server_name: str) -> str:
    if WORKING_DIR_PLACEHOLDER not in value:
        return value
    if working_dir is None:
        raise MCPServerConfigError(
            f"MCP server '{server_name}' uses {WORKING_DIR_PLACEHOLDER} but no "
            "AGENT_RUNTIME_WORKING_DIR is configured"
        )
    substituted = value.replace(WORKING_DIR_PLACEHOLDER, str(working_dir))
    resolved = Path(substituted).resolve()
    if resolved != working_dir and working_dir not in resolved.parents:
        # Guards a {WORKING_DIR}/../escape style value — the placeholder alone
        # only prevents hardcoding a path outside working_dir, not a relative
        # escape appended to it.
        raise MCPServerConfigError(
            f"MCP server '{server_name}': '{value}' resolves to '{resolved}', outside "
            f"the configured working_dir '{working_dir}'"
        )
    return substituted


@dataclass(frozen=True)
class Settings:
    agents_dir: Path
    # Restricts which agents (by frontmatter `name`) get served, out of every
    # .md file found in agents_dir. None means "serve all of them".
    agent_names: list[str] | None
    llama_base_url: str
    llama_model: str
    mcp_config_path: Path | None
    # The one directory any filesystem-capable MCP server may be scoped to
    # (via the {WORKING_DIR} placeholder in mcp_servers.json). Deliberately
    # separate from the runtime's own source and from config/, which holds
    # .env and mcp_servers.json — an agent must never be able to read those
    # off disk even though it can execute arbitrary Read/Glob calls.
    working_dir: Path
    # SQLite file backing A2A task persistence, so an in-flight task survives
    # a gateway restart (InMemoryTaskStore, the a2a-sdk default, loses every
    # task on process exit). None disables persistence (falls back to
    # InMemoryTaskStore) — useful for tests or a throwaway local run.
    task_store_path: Path | None
    # SQLite file backing the observability event log (agent_runtime/observability.py):
    # A2A requests/responses and tool calls, for later browsing/filtering by a
    # separate UI. None disables logging entirely.
    observability_db_path: Path | None
    gateway_host: str
    gateway_port: int
    public_url: str
    request_timeout: float
    mcp_call_timeout: float

    @classmethod
    def from_env(cls) -> Settings:
        host = os.environ.get("AGENT_RUNTIME_HOST", "0.0.0.0")
        port = int(os.environ.get("AGENT_RUNTIME_PORT", "9000"))
        mcp_config = os.environ.get("AGENT_RUNTIME_MCP_CONFIG")
        agent_names_raw = os.environ.get("AGENT_RUNTIME_AGENT_NAMES")
        working_dir = Path(os.environ.get("AGENT_RUNTIME_WORKING_DIR", "data")).resolve()
        secrets_dir = (Path(__file__).resolve().parent.parent / "config").resolve()
        if secrets_dir == working_dir or secrets_dir in working_dir.parents or working_dir in secrets_dir.parents:
            # config/ (.env, mcp_servers.json) must stay structurally outside
            # whatever filesystem-capable MCP servers are scoped to — an agent
            # must never be able to Read/Glob its way to those secrets.
            raise SystemExit(
                f"AGENT_RUNTIME_WORKING_DIR ('{working_dir}') overlaps with '{secrets_dir}', which "
                "holds .env and mcp_servers.json. Point AGENT_RUNTIME_WORKING_DIR at a directory "
                "structurally separate from config/."
            )
        return cls(
            agents_dir=Path(os.environ.get("AGENT_RUNTIME_AGENTS_DIR", "agents")),
            agent_names=(
                [n.strip() for n in agent_names_raw.split(",") if n.strip()] if agent_names_raw else None
            ),
            llama_base_url=os.environ.get("AGENT_RUNTIME_LLAMA_BASE_URL", "http://127.0.0.1:8080"),
            llama_model=os.environ.get("AGENT_RUNTIME_LLAMA_MODEL", "local-model"),
            mcp_config_path=Path(mcp_config) if mcp_config else None,
            working_dir=working_dir,
            task_store_path=(
                None
                if os.environ.get("AGENT_RUNTIME_TASK_STORE_PATH") == ""
                else Path(os.environ.get("AGENT_RUNTIME_TASK_STORE_PATH", "state/tasks.db"))
            ),
            observability_db_path=(
                None
                if os.environ.get("AGENT_RUNTIME_OBSERVABILITY_DB") == ""
                else Path(os.environ.get("AGENT_RUNTIME_OBSERVABILITY_DB", "state/observability.db"))
            ),
            gateway_host=host,
            gateway_port=port,
            public_url=os.environ.get("AGENT_RUNTIME_PUBLIC_URL", f"http://{host}:{port}/"),
            request_timeout=float(os.environ.get("AGENT_RUNTIME_TIMEOUT", "120")),
            mcp_call_timeout=float(os.environ.get("AGENT_RUNTIME_MCP_TIMEOUT", "60")),
        )
