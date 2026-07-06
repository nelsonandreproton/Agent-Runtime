import json
from pathlib import Path

import pytest

from agent_runtime.loader import AgentDefinition
from agent_runtime.runtime import MAX_TOOL_ITERATIONS, AgentRuntime


class FakeLLM:
    """Returns a scripted sequence of assistant messages, one per call() invocation."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []
        self.tools_seen: list[list[dict] | None] = []
        self.models_seen: list[str | None] = []

    async def chat(self, messages, tools=None, temperature=0.2, model=None):
        self.calls.append(messages)
        self.tools_seen.append(tools)
        self.models_seen.append(model)
        return self._responses.pop(0)


class FakeMCP:
    def __init__(self, aliases: list[str], call_result: tuple[str, bool] = ("ok", False)):
        self._aliases = aliases
        self._call_result = call_result
        self.calls: list[tuple[str, dict]] = []

    def known_aliases(self):
        return self._aliases

    def openai_tools_schema(self, aliases):
        return [{"type": "function", "function": {"name": a}} for a in aliases]

    async def call(self, alias, arguments):
        self.calls.append((alias, arguments))
        return self._call_result


def make_agent(tools=None, model=None) -> AgentDefinition:
    return AgentDefinition(
        name="test-agent",
        description="test",
        system_prompt="You are a test agent.",
        tools=tools,
        model=model,
        source_path=Path(__file__),
    )


@pytest.mark.asyncio
async def test_returns_final_text_with_no_tool_calls():
    llm = FakeLLM([{"role": "assistant", "content": "final answer"}])
    mcp = FakeMCP(aliases=["Read"])

    runtime = AgentRuntime(llm, mcp)
    result = await runtime.run(make_agent(tools=["Read"]), "hello")

    assert result.text == "final answer"
    assert result.tool_calls_made == 0
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_executes_a_tool_call_then_returns_final_answer():
    llm = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "Read", "arguments": json.dumps({"path": "a.py"})},
                    }
                ],
            },
            {"role": "assistant", "content": "done reading"},
        ]
    )
    mcp = FakeMCP(aliases=["Read"], call_result=("file contents", False))

    runtime = AgentRuntime(llm, mcp)
    result = await runtime.run(make_agent(tools=["Read"]), "read a.py")

    assert result.text == "done reading"
    assert result.tool_calls_made == 1
    assert mcp.calls == [("Read", {"path": "a.py"})]
    # the tool result must be fed back to the model as a "tool" message
    second_call_messages = llm.calls[1]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == "file contents"
    assert tool_messages[0]["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_tool_error_is_surfaced_to_the_model():
    llm = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "function": {"name": "Read", "arguments": "{}"}}],
            },
            {"role": "assistant", "content": "couldn't read it"},
        ]
    )
    mcp = FakeMCP(aliases=["Read"], call_result=("ENOENT", True))

    runtime = AgentRuntime(llm, mcp)
    result = await runtime.run(make_agent(tools=["Read"]), "read missing.py")

    assert result.text == "couldn't read it"
    tool_messages = [m for m in llm.calls[1] if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == "Error: ENOENT"


@pytest.mark.asyncio
async def test_gives_up_after_max_iterations():
    looping_response = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_x", "function": {"name": "Read", "arguments": "{}"}}],
    }
    llm = FakeLLM([looping_response] * MAX_TOOL_ITERATIONS)
    mcp = FakeMCP(aliases=["Read"])

    runtime = AgentRuntime(llm, mcp)
    result = await runtime.run(make_agent(tools=["Read"]), "loop forever")

    assert "maximum number of tool-call iterations" in result.text
    assert result.tool_calls_made == MAX_TOOL_ITERATIONS


@pytest.mark.asyncio
async def test_agent_inheriting_all_tools_gets_every_known_alias():
    llm = FakeLLM([{"role": "assistant", "content": "ok"}])
    mcp = FakeMCP(aliases=["Read", "Write"])

    runtime = AgentRuntime(llm, mcp)
    await runtime.run(make_agent(tools=None), "hi")

    # the tools schema passed to the LLM should include every known alias
    tool_names = {t["function"]["name"] for t in llm.tools_seen[0]}
    assert tool_names == {"Read", "Write"}


@pytest.mark.asyncio
async def test_agent_model_frontmatter_is_passed_to_the_llm_client():
    llm = FakeLLM([{"role": "assistant", "content": "ok"}])
    mcp = FakeMCP(aliases=[])

    runtime = AgentRuntime(llm, mcp)
    await runtime.run(make_agent(tools=[], model="qwen2.5-14b"), "hi")

    assert llm.models_seen[0] == "qwen2.5-14b"


@pytest.mark.asyncio
async def test_agent_without_model_frontmatter_passes_none_through():
    llm = FakeLLM([{"role": "assistant", "content": "ok"}])
    mcp = FakeMCP(aliases=[])

    runtime = AgentRuntime(llm, mcp)
    await runtime.run(make_agent(tools=[]), "hi")

    assert llm.models_seen[0] is None


@pytest.mark.asyncio
async def test_malformed_tool_call_content_is_not_leaked_at_warning_level(caplog):
    """Tool arguments can carry sensitive content (file paths, file contents an
    agent is trying to write); the codebase's INFO/DEBUG split requires raw
    content to stay behind DEBUG. A malformed call is no exception."""
    sensitive_argument = "super-secret-path/.env"
    llm = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "Read", "arguments": json.dumps({"path": sensitive_argument})}}],
            },
            {"role": "assistant", "content": "recovered"},
        ]
    )
    mcp = FakeMCP(aliases=["Read"])

    with caplog.at_level("WARNING", logger="agent_runtime.runtime"):
        runtime = AgentRuntime(llm, mcp)
        await runtime.run(make_agent(tools=["Read"]), "do something")

    warning_text = "\n".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert sensitive_argument not in warning_text


@pytest.mark.asyncio
async def test_missing_function_name_call_content_is_not_leaked_at_warning_level(caplog):
    sensitive_argument = "super-secret-path/.env"
    llm = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "arguments": json.dumps({"path": sensitive_argument})}],
            },
            {"role": "assistant", "content": "recovered"},
        ]
    )
    mcp = FakeMCP(aliases=["Read"])

    with caplog.at_level("WARNING", logger="agent_runtime.runtime"):
        runtime = AgentRuntime(llm, mcp)
        await runtime.run(make_agent(tools=["Read"]), "do something")

    warning_text = "\n".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert sensitive_argument not in warning_text


@pytest.mark.asyncio
async def test_tool_call_missing_function_name_is_surfaced_as_a_tool_error_not_a_crash():
    llm = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                # A local model dropped the "function" key entirely — a real
                # failure mode for small GGUF models, unlike the frontier APIs
                # this shape was originally designed against.
                "tool_calls": [{"id": "call_1"}],
            },
            {"role": "assistant", "content": "recovered"},
        ]
    )
    mcp = FakeMCP(aliases=["Read"])

    runtime = AgentRuntime(llm, mcp)
    result = await runtime.run(make_agent(tools=["Read"]), "do something")

    assert result.text == "recovered"
    assert mcp.calls == []
    tool_messages = [m for m in llm.calls[1] if m.get("role") == "tool"]
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert "malformed tool call" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_tool_call_missing_id_is_skipped_without_crashing():
    llm = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "Read", "arguments": "{}"}}],
            },
            {"role": "assistant", "content": "recovered"},
        ]
    )
    mcp = FakeMCP(aliases=["Read"])

    runtime = AgentRuntime(llm, mcp)
    result = await runtime.run(make_agent(tools=["Read"]), "do something")

    assert result.text == "recovered"
    assert mcp.calls == []
    # a tool_call with no `id` can never be answered by a `role: tool` message,
    # so it must not appear in the assistant turn sent onward — otherwise the
    # next request violates the tool-calling contract and fails there instead.
    second_call_messages = llm.calls[1]
    assert not any(m.get("role") == "assistant" and m.get("tool_calls") for m in second_call_messages)
    assert not any(m.get("role") == "tool" for m in second_call_messages)


@pytest.mark.asyncio
async def test_one_malformed_call_does_not_block_a_sibling_answerable_call_in_the_same_turn():
    llm = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"function": {"name": "Read", "arguments": "{}"}},  # missing id: dropped
                    {"id": "call_2", "function": {"name": "Read", "arguments": json.dumps({"path": "a.py"})}},
                ],
            },
            {"role": "assistant", "content": "done"},
        ]
    )
    mcp = FakeMCP(aliases=["Read"], call_result=("file contents", False))

    runtime = AgentRuntime(llm, mcp)
    result = await runtime.run(make_agent(tools=["Read"]), "read a.py")

    assert result.text == "done"
    assert mcp.calls == [("Read", {"path": "a.py"})]
    second_call_messages = llm.calls[1]
    assistant_turn = next(m for m in second_call_messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert [c["id"] for c in assistant_turn["tool_calls"]] == ["call_2"]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_messages == [{"role": "tool", "tool_call_id": "call_2", "content": "file contents"}]
