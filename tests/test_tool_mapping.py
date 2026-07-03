from agent_runtime.tool_mapping import resolve_agent_tools


def test_inherits_all_tools_when_requested_is_none():
    usable, skipped = resolve_agent_tools(None, ["Read", "Write"])
    assert usable == ["Read", "Write"]
    assert skipped == []


def test_usable_tools_pass_through():
    usable, skipped = resolve_agent_tools(["Read"], ["Read", "Write"])
    assert usable == ["Read"]
    assert skipped == []


def test_bash_is_always_skipped_with_a_security_reason():
    usable, skipped = resolve_agent_tools(["Read", "Bash"], ["Read", "Bash"])
    assert usable == ["Read"]
    assert len(skipped) == 1
    name, reason = skipped[0]
    assert name == "Bash"
    assert "security" in reason


def test_unmapped_tool_is_skipped_with_a_generic_reason():
    usable, skipped = resolve_agent_tools(["Frobnicate"], ["Read"])
    assert usable == []
    assert skipped == [("Frobnicate", "no connected MCP server backs this tool")]


def test_bash_is_blocked_even_if_a_misconfigured_mcp_server_claims_to_back_it():
    # available_aliases simulates an mcp_servers.json that mistakenly maps
    # "Bash" to a real MCP tool. The security block must win regardless.
    usable, skipped = resolve_agent_tools(["Bash"], ["Bash"])
    assert usable == []
    assert skipped[0][0] == "Bash"


def test_bash_is_blocked_even_when_agent_inherits_all_tools():
    usable, skipped = resolve_agent_tools(None, ["Read", "Bash"])
    assert usable == ["Read"]
    assert skipped == []
