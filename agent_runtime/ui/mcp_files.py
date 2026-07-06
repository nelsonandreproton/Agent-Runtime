"""Raw read/write of config/mcp_servers.json for the editing UI.

`load_mcp_servers()` in `agent_runtime/config.py` converts each entry into a
frozen `MCPServerConfig` and substitutes the literal `{WORKING_DIR}`
placeholder with a real resolved absolute path — correct for the running
gateway, but destructive for an editor: reading through it and writing the
result back would permanently bake a host-specific absolute path into the
file, breaking the portable placeholder and the filesystem sandboxing
convention it documents. This module instead round-trips the file as plain
JSON dicts, so every key — including the placeholder text itself and any
`_comment*` documentation keys that aren't part of `MCPServerConfig` at all —
survives a load-edit-save cycle verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path


class InvalidServerNameError(ValueError):
    """Raised when a UI-supplied MCP server name is unsafe to use."""


class MalformedMcpConfigError(ValueError):
    """Raised when config/mcp_servers.json exists but isn't valid JSON.

    A realistic state, not just a theoretical one: this file is also meant to
    be hand-edited outside the UI, so a mid-edit save, merge conflict, or
    truncated write can leave it briefly invalid. Routes are expected to
    catch this and show an error banner (mirroring how agents_routes.py
    handles a malformed agent file) rather than let it 500 the whole tab.
    """


def read_raw_mcp_config(path: Path) -> dict:
    if not path.exists():
        return {"servers": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedMcpConfigError(f"{path} is not valid JSON: {exc}") from exc


def write_raw_mcp_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_server_name(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise InvalidServerNameError(f"server name must not be empty or blank: {name!r}")
    return stripped


def find_server(data: dict, name: str) -> dict | None:
    for entry in data.get("servers", []):
        if entry.get("name") == name:
            return entry
    return None


def upsert_server(data: dict, entry: dict) -> dict:
    servers = data.get("servers", [])
    new_servers = []
    replaced = False
    for existing in servers:
        if existing.get("name") == entry["name"]:
            new_servers.append(entry)
            replaced = True
        else:
            new_servers.append(existing)
    if not replaced:
        new_servers.append(entry)
    return {**data, "servers": new_servers}


def remove_server(data: dict, name: str) -> dict:
    servers = data.get("servers", [])
    new_servers = [entry for entry in servers if entry.get("name") != name]
    return {**data, "servers": new_servers}
