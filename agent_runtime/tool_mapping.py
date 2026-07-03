"""Maps Claude Code / Cowork subagent tool names onto MCP-backed capabilities.

Agent markdown files reference tools by the names Claude Code uses (Read, Write,
Edit, Glob, Grep, Bash, ...). This runtime does not implement those tools
itself: it satisfies them through MCP servers configured in mcp_servers.json,
where each server declares which of these names it can back
(see MCPServerConfig.tool_aliases). This module only decides, given an agent's
requested tool list and the aliases actually available from connected MCP
servers, which tools the agent can use and which must be skipped.
"""

from __future__ import annotations

# Claude Code tools that this runtime deliberately never exposes over MCP,
# regardless of what a given MCP server might claim to back them with —
# either because they don't map onto a discrete MCP capability, or because
# exposing them to an agent reachable over A2A (e.g. from OutSystems ODC)
# would be a meaningful security risk.
UNSUPPORTED_TOOLS: dict[str, str] = {
    "Bash": "arbitrary shell execution is intentionally not exposed over A2A for security reasons",
    "Task": "sub-agent orchestration is not supported by this runtime",
    "TodoWrite": "session-local todo tracking does not apply to a stateless A2A agent",
    "NotebookEdit": "no MCP server is wired up for notebook editing by default",
    "WebFetch": "no MCP server is wired up for outbound web fetch by default",
    "WebSearch": "connect the Tavily MCP server and add a tool_aliases entry to enable this",
}


def resolve_agent_tools(
    requested_tools: list[str] | None,
    available_aliases: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Resolves which of an agent's requested tools can actually be served.

    Args:
        requested_tools: the agent's `tools` frontmatter field, or None if the
            agent inherits all tools available in the runtime.
        available_aliases: Claude Code tool names currently backed by a
            connected MCP server (see MCPToolsClient.known_aliases()).

    Returns:
        (usable, skipped) where usable is the list of tool names to expose to
        the LLM, and skipped is a list of (tool_name, reason) pairs for
        tools that were requested but could not be provided.
    """
    if requested_tools is None:
        return [name for name in available_aliases if name not in UNSUPPORTED_TOOLS], []

    usable: list[str] = []
    skipped: list[tuple[str, str]] = []
    for name in requested_tools:
        # Checked before availability so a misconfigured mcp_servers.json can't
        # accidentally re-enable a tool this runtime deliberately blocks.
        if name in UNSUPPORTED_TOOLS:
            skipped.append((name, UNSUPPORTED_TOOLS[name]))
        elif name in available_aliases:
            usable.append(name)
        else:
            skipped.append((name, "no connected MCP server backs this tool"))
    return usable, skipped
