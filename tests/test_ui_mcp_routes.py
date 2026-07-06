import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.testclient import TestClient

from agent_runtime.ui.mcp_files import read_raw_mcp_config
from agent_runtime.ui.mcp_routes import build_mcp_router

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "agent_runtime" / "ui" / "templates"


def seed_config() -> dict:
    return {
        "servers": [
            {
                "name": "filesystem",
                "_comment": "Prefer a globally-installed binary over npx.",
                "command": "mcp-server-filesystem",
                "_comment_working_dir": "{WORKING_DIR} is substituted at startup.",
                "args": ["{WORKING_DIR}"],
                "env": {},
                "tool_aliases": {"Read": "read_text_file", "Write": "write_file"},
            },
            {
                "name": "swarmmcp",
                "command": "/path/to/python.exe",
                "args": ["/path/to/swarmmcp/server.py"],
                "env": {"ACCESSKEY": "secret-value"},
                "tool_aliases": {"GetMyProfile": "get_my_profile"},
            },
        ]
    }


def write_seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seed_config(), indent=2), encoding="utf-8")


def make_client(config_path: Path) -> TestClient:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.include_router(build_mcp_router(config_path, templates))
    return TestClient(app)


def test_list_on_malformed_json_shows_error_banner_without_500ing(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text("{not valid json", encoding="utf-8")
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers")

    assert response.status_code == 200
    assert "not valid JSON" in response.text


def test_edit_form_on_malformed_json_returns_400_not_500(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text("{not valid json", encoding="utf-8")
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers/anything/edit")

    assert response.status_code == 400


def test_delete_on_malformed_json_returns_400_not_500(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text("{not valid json", encoding="utf-8")
    client = make_client(config_path)

    response = client.post("/ui/mcp-servers/anything/delete")

    assert response.status_code == 400


def test_list_on_nonexistent_config_returns_200_with_empty_list(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers")

    assert response.status_code == 200
    assert "filesystem" not in response.text


def test_list_shows_seeded_servers_by_name(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers")

    assert response.status_code == 200
    assert "filesystem" in response.text
    assert "swarmmcp" in response.text


def test_list_masks_env_values_but_carries_real_value_for_client_side_reveal(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers")

    assert response.status_code == 200
    # the real secret must not appear as visible text (masked span shows ****),
    # but it is present in a data-real attribute for the JS toggleMask() helper
    assert ">secret-value<" not in response.text
    assert 'data-real="secret-value"' in response.text
    assert "********" in response.text


def test_new_form_returns_200(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers/new")

    assert response.status_code == 200
    assert "<form" in response.text


def test_edit_form_for_existing_server_shows_its_command(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers/swarmmcp/edit")

    assert response.status_code == 200
    assert "/path/to/python.exe" in response.text


def test_edit_form_for_missing_server_returns_404(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)

    response = client.get("/ui/mcp-servers/does-not-exist/edit")

    assert response.status_code == 404


def test_post_new_creates_server_and_redirects(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    client = make_client(config_path)

    response = client.post(
        "/ui/mcp-servers/_new",
        data={
            "name": "newserver",
            "command": "some-command",
            "args": "arg1\narg2",
            "env_key": ["API_KEY"],
            "env_value": ["abc123"],
            "tool_aliases_key": ["Foo"],
            "tool_aliases_value": ["do_foo"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/mcp-servers?saved=1"

    data = read_raw_mcp_config(config_path)
    entry = next(s for s in data["servers"] if s["name"] == "newserver")
    assert entry["command"] == "some-command"
    assert entry["args"] == ["arg1", "arg2"]
    assert entry["env"] == {"API_KEY": "abc123"}
    assert entry["tool_aliases"] == {"Foo": "do_foo"}


def test_post_edit_preserves_comment_key_and_working_dir_placeholder(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)

    response = client.post(
        "/ui/mcp-servers/filesystem",
        data={
            "name": "filesystem",
            "command": "mcp-server-filesystem",
            "args": "{WORKING_DIR}",
            "env_key": [],
            "env_value": [],
            "tool_aliases_key": ["Read"],
            "tool_aliases_value": ["read_text_file"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    raw_text = config_path.read_text(encoding="utf-8")
    assert "{WORKING_DIR}" in raw_text

    data = read_raw_mcp_config(config_path)
    entry = next(s for s in data["servers"] if s["name"] == "filesystem")
    assert entry["_comment"] == "Prefer a globally-installed binary over npx."
    assert entry["_comment_working_dir"] == "{WORKING_DIR} is substituted at startup."
    assert entry["args"] == ["{WORKING_DIR}"]


def test_post_with_blank_name_returns_400_and_does_not_modify_file(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)
    before = config_path.read_text(encoding="utf-8")

    response = client.post(
        "/ui/mcp-servers/_new",
        data={
            "name": "   ",
            "command": "some-command",
            "args": "",
            "env_key": [],
            "env_value": [],
            "tool_aliases_key": [],
            "tool_aliases_value": [],
        },
    )

    assert response.status_code == 400
    after = config_path.read_text(encoding="utf-8")
    assert before == after
    assert "some-command" not in after


def test_post_rename_replaces_old_name_with_new_no_duplicate(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)

    before_count = len(read_raw_mcp_config(config_path)["servers"])

    response = client.post(
        "/ui/mcp-servers/swarmmcp",
        data={
            "name": "swarmmcp-renamed",
            "command": "/path/to/python.exe",
            "args": "/path/to/swarmmcp/server.py",
            "env_key": ["ACCESSKEY"],
            "env_value": ["secret-value"],
            "tool_aliases_key": ["GetMyProfile"],
            "tool_aliases_value": ["get_my_profile"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    data = read_raw_mcp_config(config_path)
    names = [s["name"] for s in data["servers"]]
    assert "swarmmcp" not in names
    assert "swarmmcp-renamed" in names
    assert len(data["servers"]) == before_count


def test_post_delete_removes_entry_and_redirects(tmp_path: Path):
    config_path = tmp_path / "mcp_servers.json"
    write_seed(config_path)
    client = make_client(config_path)

    response = client.post("/ui/mcp-servers/swarmmcp/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/mcp-servers?saved=1"

    data = read_raw_mcp_config(config_path)
    names = [s["name"] for s in data["servers"]]
    assert "swarmmcp" not in names
    assert "filesystem" in names
