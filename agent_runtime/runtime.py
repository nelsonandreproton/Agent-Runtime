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

# Tool arguments/results can contain arbitrarily large or sensitive content
# (e.g. the full text of a file read via the Read tool) — only ever logged at
# DEBUG, and truncated so a single call can't flood the log with file content.
_DEBUG_LOG_TRUNCATE_CHARS = 500


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
        for iteration in range(MAX_TOOL_ITERATIONS):
            message = await self._llm.chat(messages, tools=tools_schema or None, model=agent.model)
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                content = message.get("content") or ""
                logger.info(
                    "Agent '%s': LLM turn %d returned final answer (%d chars)",
                    agent.name,
                    iteration,
                    len(content),
                )
                logger.debug(
                    "Agent '%s': LLM turn %d final answer: %r",
                    agent.name,
                    iteration,
                    _truncate(content),
                )
                return RuntimeResult(text=content, tool_calls_made=tool_calls_made)

            logger.info(
                "Agent '%s': LLM turn %d requested %d tool call(s): %s",
                agent.name,
                iteration,
                len(tool_calls),
                [_tool_call_name(c) for c in tool_calls],
            )

            # A local model can emit a tool_call missing fields the OpenAI shape
            # requires (id and/or function.name) — small local models are more
            # prone to this than frontier APIs. Without an `id` there is no way
            # to correlate a tool response back to it, and the OpenAI/llama.cpp
            # chat-template contract requires every tool_calls entry in an
            # assistant turn to have a matching tool-role reply — so an entry we
            # can't answer must not be sent at all, or the *next* turn fails
            # instead of this one. Split into answerable vs. unanswerable before
            # appending the assistant turn.
            answerable = []
            malformed = []
            for c in tool_calls:
                (answerable if isinstance(c, dict) and c.get("id") else malformed).append(c)
            if malformed:
                logger.warning(
                    "Agent '%s': dropping %d malformed tool_call(s) with no usable id",
                    agent.name,
                    len(malformed),
                )
                logger.debug("Agent '%s': malformed tool_call(s): %r", agent.name, _truncate(repr(malformed)))
            if not answerable:
                # Nothing left to send a tool-role reply for; still record the
                # assistant's (fully malformed) turn as informational content so
                # the model has some memory of having tried, then let it retry.
                messages.append({"role": "assistant", "content": message.get("content") or ""})
                continue
            messages.append({**message, "tool_calls": answerable})

            for call in answerable:
                tool_calls_made += 1
                call_id = call["id"]
                tool_name = _tool_call_name(call)
                if tool_name is None:
                    logger.warning("Agent '%s': tool_call '%s' missing function.name", agent.name, call_id)
                    logger.debug("Agent '%s': malformed tool_call: %r", agent.name, _truncate(repr(call)))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": "Error: malformed tool call (missing function name)",
                        }
                    )
                    continue
                logger.debug(
                    "Agent '%s': calling tool '%s' with args %r",
                    agent.name,
                    tool_name,
                    _truncate(call["function"].get("arguments") or ""),
                )
                result_text, is_error = await self._execute_tool_call(call)
                logger.info(
                    "Agent '%s': tool '%s' %s (%d chars)",
                    agent.name,
                    tool_name,
                    "errored" if is_error else "returned",
                    len(result_text),
                )
                logger.debug(
                    "Agent '%s': tool '%s' result: %r",
                    agent.name,
                    tool_name,
                    _truncate(result_text),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
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


def _tool_call_name(call: object) -> str | None:
    """Best-effort tool name extraction from a possibly-malformed tool_call dict."""
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return name if isinstance(name, str) and name else None


def _truncate(text: str) -> str:
    if len(text) <= _DEBUG_LOG_TRUNCATE_CHARS:
        return text
    return f"{text[:_DEBUG_LOG_TRUNCATE_CHARS]}... ({len(text)} chars total)"
