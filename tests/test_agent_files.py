from pathlib import Path

import pytest

from agent_runtime.loader import AgentDefinition, parse_agent_markdown
from agent_runtime.ui.agent_files import (
    InvalidAgentNameError,
    serialize_agent_markdown,
    validate_agent_name,
)

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def _assert_round_trip_equal(original: AgentDefinition, reparsed: AgentDefinition) -> None:
    assert reparsed.name == original.name
    assert reparsed.description == original.description
    assert reparsed.system_prompt == original.system_prompt
    assert reparsed.tools == original.tools
    assert reparsed.model == original.model


def test_round_trip_bundled_code_reviewer_agent(tmp_path):
    original = parse_agent_markdown(AGENTS_DIR / "code-reviewer.md")

    serialized = serialize_agent_markdown(original)
    out_path = tmp_path / "code-reviewer.md"
    out_path.write_text(serialized, encoding="utf-8")

    reparsed = parse_agent_markdown(out_path)
    _assert_round_trip_equal(original, reparsed)


def test_round_trip_tools_none_omits_tools_key(tmp_path):
    original = AgentDefinition(
        name="inherits-all",
        description="test agent that inherits all tools",
        system_prompt="Do the thing.",
        tools=None,
        model="local",
        source_path=tmp_path / "inherits-all.md",
    )

    serialized = serialize_agent_markdown(original)
    assert "tools:" not in serialized

    out_path = tmp_path / "inherits-all.md"
    out_path.write_text(serialized, encoding="utf-8")

    reparsed = parse_agent_markdown(out_path)
    assert reparsed.tools is None
    _assert_round_trip_equal(original, reparsed)


def test_round_trip_model_none_omits_model_key(tmp_path):
    original = AgentDefinition(
        name="no-model",
        description="test agent with no explicit model",
        system_prompt="Do the other thing.",
        tools=["Read", "Write"],
        model=None,
        source_path=tmp_path / "no-model.md",
    )

    serialized = serialize_agent_markdown(original)
    assert "model:" not in serialized

    out_path = tmp_path / "no-model.md"
    out_path.write_text(serialized, encoding="utf-8")

    reparsed = parse_agent_markdown(out_path)
    assert reparsed.model is None
    _assert_round_trip_equal(original, reparsed)


def test_validate_agent_name_accepts_normal_name():
    assert validate_agent_name("my-agent") == "my-agent"


@pytest.mark.parametrize(
    "bad_name",
    ["../escape", "foo/bar", "foo\\bar", "", "   "],
)
def test_validate_agent_name_rejects_unsafe_names(bad_name):
    with pytest.raises(InvalidAgentNameError):
        validate_agent_name(bad_name)
