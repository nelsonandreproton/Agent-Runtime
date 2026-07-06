import json
import os
from pathlib import Path

import pytest

from agent_runtime.config import MCPServerConfigError, Settings, load_mcp_servers


def write_mcp_config(tmp_path: Path, servers: list[dict]) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return path


def test_working_dir_placeholder_is_substituted_in_args_and_env(tmp_path):
    working_dir = tmp_path / "data"
    working_dir.mkdir()
    config_path = write_mcp_config(
        tmp_path,
        [
            {
                "name": "filesystem",
                "command": "mcp-server-filesystem",
                "args": ["{WORKING_DIR}"],
                "env": {"ROOT": "{WORKING_DIR}/sub"},
                "tool_aliases": {"Read": "read_text_file"},
            }
        ],
    )

    servers = load_mcp_servers(config_path, working_dir=working_dir)

    assert Path(servers[0].args[0]) == working_dir
    assert Path(servers[0].env["ROOT"]).resolve() == (working_dir / "sub").resolve()


def test_config_without_placeholder_is_left_untouched(tmp_path):
    config_path = write_mcp_config(
        tmp_path,
        [{"name": "other", "command": "some-mcp-server", "args": ["--flag", "value"], "env": {}}],
    )

    servers = load_mcp_servers(config_path, working_dir=tmp_path / "data")

    assert servers[0].args == ["--flag", "value"]


def test_placeholder_without_a_configured_working_dir_raises():
    config_path_servers = [
        {"name": "filesystem", "command": "mcp-server-filesystem", "args": ["{WORKING_DIR}"], "env": {}}
    ]

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "mcp_servers.json"
        config_path.write_text(json.dumps({"servers": config_path_servers}), encoding="utf-8")

        with pytest.raises(MCPServerConfigError, match="no AGENT_RUNTIME_WORKING_DIR"):
            load_mcp_servers(config_path, working_dir=None)


def test_relative_escape_past_working_dir_is_rejected(tmp_path):
    working_dir = tmp_path / "data"
    working_dir.mkdir()
    config_path = write_mcp_config(
        tmp_path,
        [
            {
                "name": "filesystem",
                "command": "mcp-server-filesystem",
                # {WORKING_DIR}/../.. escapes back out past working_dir
                "args": ["{WORKING_DIR}/../.."],
                "env": {},
            }
        ],
    )

    with pytest.raises(MCPServerConfigError, match="outside the configured working_dir"):
        load_mcp_servers(config_path, working_dir=working_dir)


def test_working_dir_itself_is_allowed_not_just_subdirectories(tmp_path):
    working_dir = tmp_path / "data"
    working_dir.mkdir()
    config_path = write_mcp_config(
        tmp_path,
        [{"name": "filesystem", "command": "mcp-server-filesystem", "args": ["{WORKING_DIR}"], "env": {}}],
    )

    servers = load_mcp_servers(config_path, working_dir=working_dir)

    assert servers[0].args == [str(working_dir)]


def test_settings_from_env_rejects_working_dir_that_is_the_config_secrets_dir(monkeypatch, tmp_path):
    secrets_dir = Path(__file__).resolve().parent.parent / "config"
    monkeypatch.setenv("AGENT_RUNTIME_WORKING_DIR", str(secrets_dir))

    with pytest.raises(SystemExit, match="overlaps"):
        Settings.from_env()


def test_settings_from_env_rejects_working_dir_that_is_an_ancestor_of_config(monkeypatch, tmp_path):
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("AGENT_RUNTIME_WORKING_DIR", str(repo_root))

    with pytest.raises(SystemExit, match="overlaps"):
        Settings.from_env()


def test_settings_from_env_accepts_a_working_dir_outside_the_repo(monkeypatch, tmp_path):
    working_dir = tmp_path / "agent-data"
    monkeypatch.setenv("AGENT_RUNTIME_WORKING_DIR", str(working_dir))

    settings = Settings.from_env()

    assert settings.working_dir == working_dir.resolve()
