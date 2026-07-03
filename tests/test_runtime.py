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

    async def chat(self, messages, tools=None, temperature=0.2):
        self.calls.append(messages)
        self.tools_seen.append(tools)
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


def make_agent(tools=None) -> AgentDefinition:
    return AgentDefinition(
        name="test-agent",
        description="test",
        system_prompt="You are a test agent.",
        tools=tools,
        model=None,
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
