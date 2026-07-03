"""Markdown Agent Loader.

Parses agent definitions written in the Claude Code / Cowork subagent format:
a YAML frontmatter block (name, description, tools, model) followed by a
Markdown body used as the agent's system prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?(.*)$", re.DOTALL)


class AgentDefinitionError(ValueError):
    """Raised when a markdown agent file is missing or malformed."""


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    system_prompt: str
    tools: list[str] | None  # None means "inherit all available tools"
    model: str | None
    source_path: Path

    @property
    def inherits_all_tools(self) -> bool:
        return self.tools is None


def parse_agent_markdown(path: Path) -> AgentDefinition:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise AgentDefinitionError(
            f"{path}: missing YAML frontmatter (expected a '---' delimited block at the top of the file)"
        )

    frontmatter_raw, body = match.groups()
    try:
        frontmatter = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as exc:
        raise AgentDefinitionError(f"{path}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(frontmatter, dict):
        raise AgentDefinitionError(f"{path}: frontmatter must be a YAML mapping")

    for required in ("name", "description"):
        if required not in frontmatter:
            raise AgentDefinitionError(f"{path}: frontmatter missing required '{required}' field")

    tools = _parse_tools_field(frontmatter.get("tools"), path)

    system_prompt = body.strip()
    if not system_prompt:
        raise AgentDefinitionError(f"{path}: body (system prompt) is empty")

    model = frontmatter.get("model")
    return AgentDefinition(
        name=str(frontmatter["name"]).strip(),
        description=str(frontmatter["description"]).strip(),
        system_prompt=system_prompt,
        tools=tools,
        model=str(model).strip() if model is not None else None,
        source_path=path,
    )


def _parse_tools_field(tools_field: object, path: Path) -> list[str] | None:
    if tools_field is None:
        return None
    if isinstance(tools_field, str):
        return [t.strip() for t in tools_field.split(",") if t.strip()]
    if isinstance(tools_field, list):
        return [str(t).strip() for t in tools_field]
    raise AgentDefinitionError(f"{path}: 'tools' must be a comma-separated string or a YAML list")


def load_agent(agents_dir: Path, name: str) -> AgentDefinition:
    path = agents_dir / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"No agent definition found at {path}")
    return parse_agent_markdown(path)


def load_all_agents(agents_dir: Path) -> dict[str, AgentDefinition]:
    agents: dict[str, AgentDefinition] = {}
    for path in sorted(agents_dir.glob("*.md")):
        definition = parse_agent_markdown(path)
        agents[definition.name] = definition
    return agents
