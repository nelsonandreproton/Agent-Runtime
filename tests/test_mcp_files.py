import json
from pathlib import Path

import pytest

from agent_runtime.ui.mcp_files import (
    InvalidServerNameError,
    find_server,
    read_raw_mcp_config,
    remove_server,
    upsert_server,
    validate_server_name,
    write_raw_mcp_config,
)


def example_config() -> dict:
    return {
        "servers": [
            {
                "name": "filesystem",
                "_comment": "Prefer a globally-installed binary over 'npx -y ...'",
                "command": "mcp-server-filesystem",
                "_comment_working_dir": "{WORKING_DIR} is substituted at startup",
                "args": ["{WORKING_DIR}"],
                "env": {},
                "tool_aliases": {"Read": "read_text_file", "Write": "write_file"},
            },
            {
                "name": "swarmmcp",
                "_comment": "Example: a domain-specific MCP server",
                "_comment_command": "Use the ABSOLUTE PATH to the Python interpreter",
                "command": "/path/to/the/correct/python.exe",
                "args": ["/path/to/swarmmcp/server.py"],
                "env": {"ACCESSKEY": "<your API access key>"},
                "tool_aliases": {"GetMyProfile": "get_my_profile"},
            },
        ]
    }


def test_read_raw_mcp_config_on_nonexistent_path_returns_empty_servers(tmp_path: Path):
    missing = tmp_path / "mcp_servers.json"

    result = read_raw_mcp_config(missing)

    assert result == {"servers": []}


def test_round_trip_preserves_comment_keys_and_working_dir_placeholder(tmp_path: Path):
    original_path = tmp_path / "mcp_servers.json"
    original_path.write_text(json.dumps(example_config(), indent=2), encoding="utf-8")

    loaded = read_raw_mcp_config(original_path)

    new_path = tmp_path / "roundtrip" / "mcp_servers.json"
    write_raw_mcp_config(new_path, loaded)

    reloaded = read_raw_mcp_config(new_path)

    assert reloaded == loaded
    assert reloaded["servers"][0]["_comment_working_dir"] == "{WORKING_DIR} is substituted at startup"
    assert reloaded["servers"][0]["args"] == ["{WORKING_DIR}"]
    assert reloaded["servers"][1]["_comment_command"] == "Use the ABSOLUTE PATH to the Python interpreter"


def test_find_server_returns_matching_entry():
    data = example_config()

    found = find_server(data, "swarmmcp")

    assert found is not None
    assert found["command"] == "/path/to/the/correct/python.exe"


def test_find_server_returns_none_for_missing_name():
    data = example_config()

    assert find_server(data, "does-not-exist") is None


def test_upsert_server_replacing_existing_entry_does_not_mutate_original():
    data = example_config()
    original_servers_snapshot = json.loads(json.dumps(data["servers"]))
    replacement = {"name": "filesystem", "command": "new-command", "args": [], "env": {}, "tool_aliases": {}}

    result = upsert_server(data, replacement)

    # Original untouched.
    assert data["servers"] == original_servers_snapshot
    # Replacement happened at the same position (index 0) in the result.
    assert result["servers"][0] == replacement
    assert result["servers"][1] == data["servers"][1]
    assert len(result["servers"]) == 2


def test_upsert_server_appending_new_entry_does_not_mutate_original():
    data = example_config()
    original_servers_snapshot = json.loads(json.dumps(data["servers"]))
    new_entry = {"name": "brand-new", "command": "cmd", "args": [], "env": {}, "tool_aliases": {}}

    result = upsert_server(data, new_entry)

    assert data["servers"] == original_servers_snapshot
    assert len(result["servers"]) == 3
    assert result["servers"][-1] == new_entry


def test_remove_server_removes_matching_entry_without_mutating_original():
    data = example_config()
    original_servers_snapshot = json.loads(json.dumps(data["servers"]))

    result = remove_server(data, "filesystem")

    assert data["servers"] == original_servers_snapshot
    assert len(result["servers"]) == 1
    assert result["servers"][0]["name"] == "swarmmcp"


def test_validate_server_name_rejects_empty_and_blank():
    with pytest.raises(InvalidServerNameError):
        validate_server_name("")
    with pytest.raises(InvalidServerNameError):
        validate_server_name("   ")


def test_validate_server_name_accepts_clean_identifier():
    assert validate_server_name("my-server") == "my-server"
