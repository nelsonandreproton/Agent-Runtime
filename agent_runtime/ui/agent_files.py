"""Serialize AgentDefinition back to markdown, and validate UI-supplied agent names."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_runtime.loader import AgentDefinition


def serialize_agent_markdown(definition: AgentDefinition) -> str:
    frontmatter: dict[str, object] = {
        "name": definition.name,
        "description": definition.description,
    }
    if definition.tools is not None:
        frontmatter["tools"] = definition.tools
    if definition.model is not None:
        frontmatter["model"] = definition.model

    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False)
    return "---\n" + yaml_block + "---\n\n" + definition.system_prompt.strip() + "\n"


class InvalidAgentNameError(ValueError):
    """Raised when a UI-supplied agent name is unsafe to use as a filename component."""


def validate_agent_name(name: str) -> str:
    stripped = name.strip()
    if not stripped or stripped == ".":
        raise InvalidAgentNameError(f"agent name must not be empty or blank: {name!r}")
    if ".." in stripped or "/" in stripped or "\\" in stripped:
        raise InvalidAgentNameError(f"agent name contains path separators or '..': {name!r}")
    if Path(stripped).name != stripped:
        raise InvalidAgentNameError(f"agent name is not a valid filename component: {name!r}")
    return stripped
