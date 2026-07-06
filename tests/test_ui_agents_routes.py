from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.testclient import TestClient

from agent_runtime.loader import parse_agent_markdown
from agent_runtime.ui.agents_routes import build_agents_router

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "agent_runtime" / "ui" / "templates"

GOOD_AGENT_MD = """---
name: {name}
description: {description}
tools: Read, Glob
model: local
---

You are a helpful assistant for {name}.
"""

MALFORMED_AGENT_MD = """This file has no YAML frontmatter at all.
"""


def make_client(agents_dir: Path) -> TestClient:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.include_router(build_agents_router(agents_dir, templates))
    return TestClient(app)


def seed_agent(agents_dir: Path, name: str, description: str = "does things") -> None:
    (agents_dir / f"{name}.md").write_text(
        GOOD_AGENT_MD.format(name=name, description=description), encoding="utf-8"
    )


def test_list_shows_seeded_agents(tmp_path: Path):
    seed_agent(tmp_path, "code-reviewer")
    seed_agent(tmp_path, "security-reviewer")
    client = make_client(tmp_path)

    response = client.get("/ui/agents")

    assert response.status_code == 200
    assert "code-reviewer" in response.text
    assert "security-reviewer" in response.text


def test_list_shows_malformed_file_without_500ing(tmp_path: Path):
    seed_agent(tmp_path, "good-agent")
    (tmp_path / "broken.md").write_text(MALFORMED_AGENT_MD, encoding="utf-8")
    client = make_client(tmp_path)

    response = client.get("/ui/agents")

    assert response.status_code == 200
    assert "good-agent" in response.text
    assert "broken.md" in response.text
    assert "malformed" in response.text


def test_new_agent_form_returns_200_with_a_form(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/ui/agents/new")

    assert response.status_code == 200
    assert "<form" in response.text


def test_edit_form_for_existing_agent_shows_its_description(tmp_path: Path):
    seed_agent(tmp_path, "code-reviewer", description="Reviews the code thoroughly")
    client = make_client(tmp_path)

    response = client.get("/ui/agents/code-reviewer/edit")

    assert response.status_code == 200
    assert "Reviews the code thoroughly" in response.text


def test_edit_form_for_nonexistent_agent_returns_404(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/ui/agents/nonexistent/edit")

    assert response.status_code == 404


def test_post_new_creates_agent_file_and_redirects(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/_new",
        data={
            "name": "new-agent",
            "description": "A brand new agent",
            "tools": "Read\nGlob",
            "model": "local",
            "system_prompt": "You are new.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/agents?saved=1"

    written_path = tmp_path / "new-agent.md"
    assert written_path.exists()
    agent = parse_agent_markdown(written_path)
    assert agent.name == "new-agent"
    assert agent.description == "A brand new agent"
    assert agent.tools == ["Read", "Glob"]
    assert agent.model == "local"
    assert agent.system_prompt == "You are new."


def test_post_new_with_unsafe_name_does_not_escape_agents_dir(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/_new",
        data={
            "name": "../escape",
            "description": "malicious",
            "tools": "",
            "model": "",
            "system_prompt": "x",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "error" in response.text.lower() or "agent name" in response.text.lower()
    escaped_path = tmp_path.parent / "escape.md"
    assert not escaped_path.exists()
    assert not any(tmp_path.glob("*escape*"))


def test_post_rename_removes_old_file_and_creates_new_one(tmp_path: Path):
    seed_agent(tmp_path, "old-name", description="original")
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/old-name",
        data={
            "name": "new-name",
            "description": "original",
            "tools": "Read, Glob",
            "model": "local",
            "system_prompt": "You are helpful.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert not (tmp_path / "old-name.md").exists()
    assert (tmp_path / "new-name.md").exists()
    agent = parse_agent_markdown(tmp_path / "new-name.md")
    assert agent.name == "new-name"


def test_list_with_saved_query_param_shows_restart_banner(tmp_path: Path):
    seed_agent(tmp_path, "code-reviewer")
    client = make_client(tmp_path)

    response = client.get("/ui/agents", params={"saved": "1"})

    assert response.status_code == 200
    assert "Restart the gateway" in response.text


def test_post_new_with_reserved_sentinel_name_is_rejected(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/_new",
        data={
            "name": "_new",
            "description": "sneaky",
            "tools": "",
            "model": "",
            "system_prompt": "x",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert not (tmp_path / "_new.md").exists()


def test_list_does_not_build_delete_confirmation_via_inline_js_string_interpolation(tmp_path: Path):
    """A name containing a single quote must not be able to break out of an
    inline onsubmit="...confirmDelete('...name...')" JS string literal —
    Jinja2's HTML-attribute escaping alone doesn't protect that context,
    since the browser HTML-decodes the attribute before the JS parser runs.
    The fix reads the name via a data-* attribute instead of interpolating
    it into a JS string, so this asserts that pattern is actually in use."""
    seed_agent(tmp_path, "normal-agent")
    client = make_client(tmp_path)

    response = client.get("/ui/agents")

    assert response.status_code == 200
    assert "confirmDeleteForm(this)" in response.text
    assert "confirmDelete('" not in response.text


def test_delete_rejects_a_backslash_traversal_url_segment(tmp_path: Path):
    """Starlette's default single-segment path matcher blocks a literal '/'
    in {name} but NOT a backslash, which pathlib treats as a real separator
    on Windows. Without validating the URL segment itself (not just the
    submitted form name), this could delete a file outside agents_dir."""
    victim_dir = tmp_path.parent / "victim"
    victim_dir.mkdir(exist_ok=True)
    victim = victim_dir / "secret.md"
    victim.write_text("do not delete me", encoding="utf-8")
    client = make_client(tmp_path)

    response = client.post("/ui/agents/..%5C..%5Cvictim%5Csecret/delete")

    assert response.status_code == 400
    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "do not delete me"


def test_edit_rejects_a_backslash_traversal_url_segment(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/ui/agents/..%5C..%5Csome-secret-file/edit")

    assert response.status_code == 400


def test_save_rejects_a_backslash_traversal_url_segment_before_any_unlink(tmp_path: Path):
    victim_dir = tmp_path.parent / "victim2"
    victim_dir.mkdir(exist_ok=True)
    victim = victim_dir / "secret.md"
    victim.write_text("do not delete me", encoding="utf-8")
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/..%5C..%5Cvictim2%5Csecret",
        data={
            "name": "renamed",
            "description": "x",
            "tools": "",
            "model": "",
            "system_prompt": "x",
        },
    )

    assert response.status_code == 400
    assert victim.exists()


def test_edit_form_for_existing_agent_ignores_a_malformed_sibling_file(tmp_path: Path):
    seed_agent(tmp_path, "code-reviewer", description="Reviews the code thoroughly")
    (tmp_path / "broken.md").write_text(MALFORMED_AGENT_MD, encoding="utf-8")
    client = make_client(tmp_path)

    response = client.get("/ui/agents/code-reviewer/edit")

    assert response.status_code == 200
    assert "Reviews the code thoroughly" in response.text


def test_post_rename_onto_an_existing_agent_name_is_rejected_not_overwritten(tmp_path: Path):
    seed_agent(tmp_path, "keep-me", description="do not lose this")
    seed_agent(tmp_path, "rename-me", description="original")
    client = make_client(tmp_path)

    response = client.post(
        "/ui/agents/rename-me",
        data={
            "name": "keep-me",
            "description": "trying to overwrite",
            "tools": "",
            "model": "",
            "system_prompt": "x",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    kept = parse_agent_markdown(tmp_path / "keep-me.md")
    assert kept.description == "do not lose this"
    assert (tmp_path / "rename-me.md").exists()


def test_post_delete_removes_file_and_redirects(tmp_path: Path):
    seed_agent(tmp_path, "to-delete")
    client = make_client(tmp_path)

    response = client.post("/ui/agents/to-delete/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/agents?saved=1"
    assert not (tmp_path / "to-delete.md").exists()
