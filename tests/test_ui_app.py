from pathlib import Path

from starlette.testclient import TestClient

from agent_runtime.observability import LogStore
from agent_runtime.ui.app import build_ui_app

GOOD_AGENT_MD = """---
name: test-agent
description: a test agent
---

Hello.
"""


def make_client(tmp_path: Path, log_store=None) -> TestClient:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "test-agent.md").write_text(GOOD_AGENT_MD, encoding="utf-8")
    mcp_config_path = tmp_path / "mcp_servers.json"
    app = build_ui_app(
        agents_dir=agents_dir,
        mcp_config_path=mcp_config_path,
        log_store=log_store,
        agent_names=["test-agent"],
    )
    return TestClient(app)


def test_root_redirects_to_agents_tab(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 303, 307, 308)
    assert response.headers["location"] == "/ui/agents"


def test_all_three_tabs_are_mounted_and_reachable(tmp_path: Path):
    client = make_client(tmp_path)

    assert client.get("/ui/agents").status_code == 200
    assert client.get("/ui/mcp-servers").status_code == 200
    assert client.get("/ui/logs").status_code == 200


def test_agents_tab_reflects_the_real_agents_dir(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/ui/agents")

    assert "test-agent" in response.text


def test_logs_tab_uses_the_provided_log_store(tmp_path: Path):
    log_store = LogStore(tmp_path / "observability.db")
    log_store.record("task-1", "test-agent", "request", "hello")
    client = make_client(tmp_path, log_store=log_store)

    response = client.get("/ui/logs")

    assert response.status_code == 200
    assert "task-1"[:8] in response.text or "task-1" in response.text
    log_store.close()


def test_cross_origin_post_is_rejected(tmp_path: Path):
    """A browser will deliver a simple form POST from ANY page open on the
    operator's machine to this port, since there's no auth/CSRF token. The
    Origin header is browser-set and not spoofable by page JS, so rejecting
    a mismatched Origin on mutating requests closes that drive-by vector."""
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/nonexistent/delete",
        headers={"origin": "http://evil.example.com"},
    )

    assert response.status_code == 403


def test_same_origin_post_is_allowed_through_to_the_route(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/test-agent/delete",
        headers={"origin": "http://testserver"},
    )

    assert response.status_code != 403


def test_post_with_no_origin_header_is_allowed_through(tmp_path: Path):
    """Non-browser clients (curl, scripts) don't send an Origin header at
    all — only reject when Origin is present AND mismatched."""
    client = make_client(tmp_path)

    response = client.post("/ui/agents/test-agent/delete")

    assert response.status_code != 403


def test_get_requests_are_never_blocked_by_the_origin_check(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/ui/agents", headers={"origin": "http://evil.example.com"})

    assert response.status_code == 200


def test_static_files_are_served(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
