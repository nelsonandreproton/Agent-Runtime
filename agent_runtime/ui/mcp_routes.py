"""FastAPI routes for the MCP Servers tab of the local admin UI.

Manages config/mcp_servers.json directly on disk: list, create, edit, rename,
delete server entries. No database — every operation reads/writes the raw JSON
file via agent_runtime.ui.mcp_files. Deliberately does NOT go through
agent_runtime.config.load_mcp_servers(): that function substitutes the literal
`{WORKING_DIR}` placeholder with a real resolved path, which would permanently
bake a host-specific path into the file on save. See mcp_files.py's module
docstring for the full rationale.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from agent_runtime.ui.mcp_files import (
    InvalidServerNameError,
    MalformedMcpConfigError,
    find_server,
    read_raw_mcp_config,
    remove_server,
    upsert_server,
    validate_server_name,
    write_raw_mcp_config,
)

# Sentinel used in the POST /{name} route (and the "New MCP Server" form's
# action) to mean "there is no original entry to look up or rename away
# from" — the create case. Mirrors the same NEW_AGENT_SENTINEL trick used by
# agent_runtime/ui/agents_routes.py for the Agents tab.
NEW_SERVER_SENTINEL = "_new"

# _comment*-prefixed keys are arbitrary documentation strings that live
# alongside a server entry (see config/mcp_servers.example.json) but aren't
# editable fields — carried through unchanged on save, shown read-only here.
_COMMENT_KEY_PREFIX = "_comment"


def _comment_fields(entry: dict) -> dict[str, str]:
    return {key: value for key, value in entry.items() if key.startswith(_COMMENT_KEY_PREFIX)}


def _args_to_textarea(args: list[str] | None) -> str:
    if not args:
        return ""
    return "\n".join(args)


def _args_from_textarea(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _kv_pairs_from_form(keys: list[str], values: list[str]) -> dict[str, str]:
    """Zips parallel key/value form field lists into a dict, dropping empty keys."""
    return {key.strip(): value for key, value in zip(keys, values) if key.strip()}


def build_mcp_router(mcp_config_path: Path, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/ui/mcp-servers")

    @router.get("")
    def list_servers(request: Request):
        restart_required = request.query_params.get("saved") is not None
        try:
            data = read_raw_mcp_config(mcp_config_path)
        except MalformedMcpConfigError as exc:
            # Mirrors agents_routes.py's per-file malformed handling: this is
            # a realistic state (the file is also hand-edited outside the
            # UI), so show an error banner instead of 500ing the whole tab.
            return templates.TemplateResponse(
                request,
                "mcp_list.html",
                {
                    "active_tab": "mcp-servers",
                    "restart_required": restart_required,
                    "servers": [],
                    "error": str(exc),
                },
                status_code=200,
            )
        return templates.TemplateResponse(
            request,
            "mcp_list.html",
            {
                "active_tab": "mcp-servers",
                "restart_required": restart_required,
                "servers": data.get("servers", []),
            },
        )

    @router.get("/new")
    def new_server_form(request: Request):
        return templates.TemplateResponse(
            request,
            "mcp_edit.html",
            {
                "active_tab": "mcp-servers",
                "is_new": True,
                "original_name": NEW_SERVER_SENTINEL,
                "name": "",
                "command": "",
                "args_text": "",
                "env": {},
                "tool_aliases": {},
                "comments": {},
            },
        )

    @router.get("/{name}/edit")
    def edit_server_form(request: Request, name: str):
        try:
            data = read_raw_mcp_config(mcp_config_path)
        except MalformedMcpConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        entry = find_server(data, name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"No MCP server named {name!r}")

        return templates.TemplateResponse(
            request,
            "mcp_edit.html",
            {
                "active_tab": "mcp-servers",
                "is_new": False,
                "original_name": name,
                "name": entry.get("name", ""),
                "command": entry.get("command", ""),
                "args_text": _args_to_textarea(entry.get("args")),
                "env": entry.get("env", {}),
                "tool_aliases": entry.get("tool_aliases", {}),
                "comments": _comment_fields(entry),
            },
        )

    @router.post("/{name}")
    async def save_server(request: Request, name: str):
        form = await request.form()
        submitted_name = str(form.get("name", ""))
        command = str(form.get("command", "")).strip()
        args_list = _args_from_textarea(str(form.get("args", "")))
        env_dict = _kv_pairs_from_form(form.getlist("env_key"), form.getlist("env_value"))
        tool_aliases_dict = _kv_pairs_from_form(
            form.getlist("tool_aliases_key"), form.getlist("tool_aliases_value")
        )

        is_new = name == NEW_SERVER_SENTINEL

        try:
            data = read_raw_mcp_config(mcp_config_path)
        except MalformedMcpConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            new_name = validate_server_name(submitted_name)
        except InvalidServerNameError as exc:
            existing = None if is_new else find_server(data, name)
            return templates.TemplateResponse(
                request,
                "mcp_edit.html",
                {
                    "active_tab": "mcp-servers",
                    "is_new": is_new,
                    "original_name": name,
                    "name": submitted_name,
                    "command": command,
                    "args_text": _args_to_textarea(args_list),
                    "env": env_dict,
                    "tool_aliases": tool_aliases_dict,
                    "comments": _comment_fields(existing) if existing else {},
                    "error": str(exc),
                },
                status_code=400,
            )

        if is_new:
            new_entry = {
                "name": new_name,
                "command": command,
                "args": args_list,
                "env": env_dict,
                "tool_aliases": tool_aliases_dict,
            }
        else:
            existing = find_server(data, name) or {}
            new_entry = {
                **existing,
                "name": new_name,
                "command": command,
                "args": args_list,
                "env": env_dict,
                "tool_aliases": tool_aliases_dict,
            }
            if new_name != name:
                # Renaming: upsert-by-new-name alone would just append a second
                # entry rather than replace the old one, since it matches by
                # name. Remove the old-named entry first, then upsert the new one.
                data = remove_server(data, name)

        updated = upsert_server(data, new_entry)
        write_raw_mcp_config(mcp_config_path, updated)

        return RedirectResponse(url="/ui/mcp-servers?saved=1", status_code=303)

    @router.post("/{name}/delete")
    def delete_server(name: str):
        try:
            data = read_raw_mcp_config(mcp_config_path)
        except MalformedMcpConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updated = remove_server(data, name)
        write_raw_mcp_config(mcp_config_path, updated)
        return RedirectResponse(url="/ui/mcp-servers?saved=1", status_code=303)

    return router
