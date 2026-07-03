"""Agent Runtime: the core loop that turns a user message into an agent reply.

Builds the message list from the agent's system prompt, calls the LLM with
the tools it's allowed to use, executes any tool calls it requests via MCP,
and repeats until the model returns a final answer or a safety cap is hit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .llm_client import LlamaServerClient
from .loader import AgentDefinition
from .mcp_client import MCPToolsClient
from .tool_mapping import resolve_agent_tools

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8


@dataclass(frozen=True)
class RuntimeResult:
    text: str
    tool_calls_made: int


class AgentRuntime:
    def __init__(self, llm_client: LlamaServerClient, mcp_client: MCPToolsClient):
        self._llm = llm_client
        self._mcp = mcp_client

    async def run(self, agent: AgentDefinition, user_message: str) -> RuntimeResult:
        usable_tools, skipped = resolve_agent_tools(agent.tools, self._mcp.known_aliases())
        for name, reason in skipped:
            logger.info("Agent '%s': tool '%s' not exposed (%s)", agent.name, name, reason)

        tools_schema = self._mcp.openai_tools_schema(usable_tools)
        messages: list[dict] = [
            {"role": "system", "content": agent.system_prompt},
            {"role": "user", "content": user_message},
        ]

        tool_calls_made = 0
        for _ in range(MAX_TOOL_ITERATIONS):
            message = await self._llm.chat(messages, tools=tools_schema or None)
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                return RuntimeResult(text=message.get("content") or "", tool_calls_made=tool_calls_made)

            messages.append(message)
            for call in tool_calls:
                tool_calls_made += 1
                result_text, is_error = await self._execute_tool_call(call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": f"Error: {result_text}" if is_error else result_text,
                    }
                )

        logger.warning("Agent '%s' hit the %d-iteration tool-call cap", agent.name, MAX_TOOL_ITERATIONS)
        return RuntimeResult(
            text="Reached the maximum number of tool-call iterations without a final answer.",
            tool_calls_made=tool_calls_made,
        )

    async def _execute_tool_call(self, call: dict) -> tuple[str, bool]:
        function = call["function"]
        name = function["name"]
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            return f"Could not parse arguments for tool '{name}'", True
        return await self._mcp.call(name, arguments)
