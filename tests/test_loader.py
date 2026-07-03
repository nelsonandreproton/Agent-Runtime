from pathlib import Path

import pytest

from agent_runtime.loader import AgentDefinitionError, load_agent, load_all_agents, parse_agent_markdown

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def test_parses_bundled_code_reviewer_agent():
    agent = load_agent(AGENTS_DIR, "code-reviewer")

    assert agent.name == "code-reviewer"
    assert "review" in agent.description.lower()
    assert agent.tools == ["Read", "Glob"]
    assert agent.model == "local"
    assert "senior code reviewer" in agent.system_prompt.lower()
    assert not agent.inherits_all_tools


def test_missing_agent_file_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_agent(AGENTS_DIR, "does-not-exist")


def test_load_all_agents_finds_bundled_agent():
    agents = load_all_agents(AGENTS_DIR)
    assert "code-reviewer" in agents


def test_tools_field_omitted_means_inherit_all(tmp_path):
    path = tmp_path / "inherits-all.md"
    path.write_text(
        "---\nname: inherits-all\ndescription: test agent\n---\nDo the thing.\n",
        encoding="utf-8",
    )
    agent = parse_agent_markdown(path)
    assert agent.tools is None
    assert agent.inherits_all_tools


def test_tools_field_accepts_yaml_list(tmp_path):
    path = tmp_path / "list-tools.md"
    path.write_text(
        "---\nname: list-tools\ndescription: test agent\ntools:\n  - Read\n  - Write\n---\nBody.\n",
        encoding="utf-8",
    )
    agent = parse_agent_markdown(path)
    assert agent.tools == ["Read", "Write"]


def test_missing_frontmatter_raises(tmp_path):
    path = tmp_path / "no-frontmatter.md"
    path.write_text("Just a body, no frontmatter.\n", encoding="utf-8")
    with pytest.raises(AgentDefinitionError, match="missing YAML frontmatter"):
        parse_agent_markdown(path)


def test_missing_required_field_raises(tmp_path):
    path = tmp_path / "missing-description.md"
    path.write_text("---\nname: incomplete\n---\nBody.\n", encoding="utf-8")
    with pytest.raises(AgentDefinitionError, match="description"):
        parse_agent_markdown(path)


def test_empty_body_raises(tmp_path):
    path = tmp_path / "empty-body.md"
    path.write_text("---\nname: empty\ndescription: test\n---\n   \n", encoding="utf-8")
    with pytest.raises(AgentDefinitionError, match="empty"):
        parse_agent_markdown(path)
