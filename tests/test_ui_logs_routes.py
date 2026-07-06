import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.testclient import TestClient

from agent_runtime.observability import LogStore
from agent_runtime.ui.logs_routes import build_logs_router, normalize_end_filter

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "agent_runtime" / "ui" / "templates"


def make_client(log_store: LogStore | None, agent_names: list[str]) -> TestClient:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.include_router(build_logs_router(log_store, agent_names, templates))
    return TestClient(app)


def test_list_with_no_filters_shows_events_from_every_task(tmp_path: Path):
    log_store = LogStore(tmp_path / "observability.db")
    log_store.record("task-1", "agent-a", "request", "hello from agent-a request")
    log_store.record("task-1", "agent-a", "response", "reply from agent-a")
    log_store.record("task-2", "agent-b", "request", "hello from agent-b request")
    client = make_client(log_store, ["agent-a", "agent-b"])

    response = client.get("/ui/logs")

    assert response.status_code == 200
    assert "hello from agent-a request" in response.text
    assert "hello from agent-b request" in response.text
    log_store.close()


def test_filtering_by_agent_excludes_the_other_agents_content(tmp_path: Path):
    log_store = LogStore(tmp_path / "observability.db")
    log_store.record("task-1", "agent-a", "request", "only agent-a should show this marker")
    log_store.record("task-2", "agent-b", "request", "only agent-b should show this other marker")
    client = make_client(log_store, ["agent-a", "agent-b"])

    response = client.get("/ui/logs", params={"agent": "agent-a"})

    assert response.status_code == 200
    assert "only agent-a should show this marker" in response.text
    assert "only agent-b should show this other marker" not in response.text
    log_store.close()


def test_task_detail_shows_events_in_order(tmp_path: Path):
    log_store = LogStore(tmp_path / "observability.db")
    log_store.record("task-1", "agent-a", "request", "the request content")
    log_store.record("task-1", "agent-a", "response", "the response content")
    client = make_client(log_store, ["agent-a"])

    response = client.get("/ui/logs/task-1")

    assert response.status_code == 200
    body = response.text
    assert body.index("request") < body.index("response")
    assert "the request content" in body
    assert "the response content" in body
    log_store.close()


def test_task_detail_for_unknown_task_id_returns_200_with_no_events_message(tmp_path: Path):
    log_store = LogStore(tmp_path / "observability.db")
    log_store.record("task-1", "agent-a", "request", "some content")
    client = make_client(log_store, ["agent-a"])

    response = client.get("/ui/logs/never-recorded-task-id")

    assert response.status_code == 200
    assert "no events" in response.text.lower()
    log_store.close()


def test_list_link_targets_the_full_task_id_not_the_truncated_display_text(tmp_path: Path):
    """The list page shows only the first 8 characters of a task_id, but the
    <a href> must still point at the FULL id — a real UUID-shaped task_id is
    much longer than 8 chars, so a display-truncation bug that leaked into
    the href would silently 404 every click from the list to the detail
    page. Follow the actual link, not just a hardcoded task_id in a GET."""
    task_id = "170aaac9-29db-4c7d-87e0-b188d19cb09f"  # 36 chars, like a real a2a-sdk task id
    log_store = LogStore(tmp_path / "observability.db")
    log_store.record(task_id, "agent-a", "request", "the full request content")
    client = make_client(log_store, ["agent-a"])

    list_response = client.get("/ui/logs")
    assert list_response.status_code == 200

    match = re.search(r'href="(/ui/logs/[^"]+)"', list_response.text)
    assert match is not None, "no task detail link found on the list page"
    href = match.group(1)
    assert href == f"/ui/logs/{task_id}"

    detail_response = client.get(href)
    assert detail_response.status_code == 200
    assert "the full request content" in detail_response.text
    log_store.close()


def test_disabled_log_store_shows_disabled_message_instead_of_crashing():
    client = make_client(None, ["agent-a"])

    response = client.get("/ui/logs")

    assert response.status_code == 200
    assert "disabled" in response.text.lower()


def test_disabled_log_store_task_detail_also_handled_gracefully():
    client = make_client(None, ["agent-a"])

    response = client.get("/ui/logs/some-task-id")

    assert response.status_code == 200
    assert "disabled" in response.text.lower()


def test_end_filter_normalization_covers_the_whole_minute():
    # A bare datetime-local value has no seconds. Once padded, it must still
    # work as an inclusive upper bound (timestamp <= end) against a full ISO
    # timestamp recorded later within that same minute.
    normalized = normalize_end_filter("2026-07-06T14:30")

    assert "2026-07-06T14:30:45.123456+00:00" <= normalized
    assert "2026-07-06T14:31:00.000000+00:00" > normalized


def test_end_filter_normalization_passes_through_values_that_already_have_seconds():
    already_has_seconds = "2026-07-06T14:30:45"

    assert normalize_end_filter(already_has_seconds) == already_has_seconds


def test_end_filter_normalization_passes_through_none():
    assert normalize_end_filter(None) is None
