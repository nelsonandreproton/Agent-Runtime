"""FastAPI routes for the Agents tab of the local admin UI.

Manages agents/*.md files directly on disk: list, create, edit, rename, delete.
No database — every operation reads/writes markdown files via agent_runtime.loader
and agent_runtime.ui.agent_files.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from agent_runtime.loader import AgentDefinition, AgentDefinitionError, parse_agent_markdown
from agent_runtime.ui.agent_files import InvalidAgentNameError, serialize_agent_markdown, validate_agent_name

# Sentinel used in the POST /{name} route to mean "there is no original file to
# rename away from" — the create case. validate_agent_name does NOT reject this
# string on its own (it has no path separators or ".."), so save_agent explicitly
# rejects an attempt to name a real agent "_new" below — otherwise renaming an
# agent away from that name would orphan "_new.md" (save_agent would treat the
# rename as a fresh create and skip deleting the old file).
NEW_AGENT_SENTINEL = "_new"


def _tools_to_textarea(tools: list[str] | None) -> str:
    """Newline-joined for the edit form's textarea. Empty/None -> empty string."""
    if not tools:
        return ""
    return "\n".join(tools)


def _tools_from_form(raw: str) -> list[str] | None:
    """Split on newlines and commas, strip, drop empties. Empty result -> None
    (matches loader._parse_tools_field's "None means inherit all" semantics)."""
    parts: list[str] = []
    for line in raw.splitlines():
        for chunk in line.split(","):
            chunk = chunk.strip()
            if chunk:
                parts.append(chunk)
    return parts or None


def build_agents_router(agents_dir: Path, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/ui/agents")

    @router.get("")
    def list_agents(request: Request):
        rows = []
        for path in sorted(agents_dir.glob("*.md")):
            try:
                agent = parse_agent_markdown(path)
            except AgentDefinitionError as exc:
                rows.append({"ok": False, "path": path.name, "error": str(exc)})
            else:
                rows.append({"ok": True, "agent": agent})

        restart_required = request.query_params.get("saved") is not None
        return templates.TemplateResponse(
            request,
            "agents_list.html",
            {"active_tab": "agents", "restart_required": restart_required, "rows": rows},
        )

    @router.get("/new")
    def new_agent_form(request: Request):
        return templates.TemplateResponse(
            request,
            "agent_edit.html",
            {
                "active_tab": "agents",
                "is_new": True,
                "original_name": NEW_AGENT_SENTINEL,
                "name": "",
                "description": "",
                "tools_text": "",
                "model": "",
                "system_prompt": "",
            },
        )

    @router.get("/{name}/edit")
    def edit_agent_form(request: Request, name: str):
        # name comes straight from the URL path segment. Starlette's default
        # single-segment matcher blocks a literal "/" but NOT a backslash,
        # which pathlib treats as a real separator on Windows — so this must
        # be validated exactly like a submitted form name, or "..\..\secret"
        # style values reach the filesystem below.
        try:
            name = validate_agent_name(name)
        except InvalidAgentNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Don't use load_all_agents here: it raises on the FIRST malformed
        # sibling file, which would 500 this page for an unrelated broken
        # agent elsewhere in agents_dir. Look up only the one file requested.
        path = agents_dir / f"{name}.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No agent named {name!r}")
        try:
            agent = parse_agent_markdown(path)
        except AgentDefinitionError as exc:
            raise HTTPException(status_code=400, detail=f"{name!r} is malformed: {exc}") from exc

        return templates.TemplateResponse(
            request,
            "agent_edit.html",
            {
                "active_tab": "agents",
                "is_new": False,
                "original_name": name,
                "name": agent.name,
                "description": agent.description,
                "tools_text": _tools_to_textarea(agent.tools),
                "model": agent.model or "",
                "system_prompt": agent.system_prompt,
            },
        )

    @router.post("/{name}")
    def save_agent(
        request: Request,
        name: str,
        agent_name: str = Form(..., alias="name"),
        description: str = Form(...),
        tools: str = Form(""),
        model: str = Form(""),
        system_prompt: str = Form(...),
    ):
        is_new = name == NEW_AGENT_SENTINEL

        # Same reasoning as edit_agent_form: `name` is a raw URL segment and
        # must be validated before it's used to build a path for the old-file
        # unlink below. The _new sentinel is the one legitimate exception.
        if not is_new:
            try:
                name = validate_agent_name(name)
            except InvalidAgentNameError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            new_name = validate_agent_name(agent_name)
            if new_name == NEW_AGENT_SENTINEL:
                raise InvalidAgentNameError(f"agent name {NEW_AGENT_SENTINEL!r} is reserved, choose another name")
            if new_name != name and (agents_dir / f"{new_name}.md").exists():
                raise InvalidAgentNameError(f"an agent named {new_name!r} already exists")
        except InvalidAgentNameError as exc:
            return templates.TemplateResponse(
                request,
                "agent_edit.html",
                {
                    "active_tab": "agents",
                    "is_new": is_new,
                    "original_name": name,
                    "name": agent_name,
                    "description": description,
                    "tools_text": tools,
                    "model": model,
                    "system_prompt": system_prompt,
                    "error": str(exc),
                },
                status_code=400,
            )

        definition = AgentDefinition(
            name=new_name,
            description=description.strip(),
            system_prompt=system_prompt.strip(),
            tools=_tools_from_form(tools),
            model=model.strip() or None,
            source_path=Path(),
        )
        markdown = serialize_agent_markdown(definition)
        (agents_dir / f"{new_name}.md").write_text(markdown, encoding="utf-8")

        if not is_new and new_name != name:
            (agents_dir / f"{name}.md").unlink(missing_ok=True)

        return RedirectResponse(url="/ui/agents?saved=1", status_code=303)

    @router.post("/{name}/delete")
    def delete_agent(name: str):
        try:
            name = validate_agent_name(name)
        except InvalidAgentNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        (agents_dir / f"{name}.md").unlink(missing_ok=True)
        return RedirectResponse(url="/ui/agents?saved=1", status_code=303)

    return router
