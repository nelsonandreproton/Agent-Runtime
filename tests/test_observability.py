import sqlite3
from pathlib import Path

from agent_runtime.observability import LogStore


def test_record_and_query_round_trip(tmp_path: Path):
    store = LogStore(tmp_path / "observability.db")

    store.record("task-1", "agent-a", "request", "hello")
    store.record("task-1", "agent-a", "response", "hi there")

    rows = store.query(agent_name="agent-a")
    assert [r["event_type"] for r in rows] == ["response", "request"]  # newest-first
    store.close()


def test_get_task_events_returns_oldest_first(tmp_path: Path):
    store = LogStore(tmp_path / "observability.db")
    store.record("task-1", "agent-a", "request", "hello")
    store.record("task-1", "agent-a", "tool_call", "Read(x)")
    store.record("task-1", "agent-a", "response", "done")

    events = store.get_task_events("task-1")

    assert [e["event_type"] for e in events] == ["request", "tool_call", "response"]
    store.close()


def test_query_filters_by_agent_name(tmp_path: Path):
    store = LogStore(tmp_path / "observability.db")
    store.record("task-1", "agent-a", "request", "from a")
    store.record("task-2", "agent-b", "request", "from b")

    rows = store.query(agent_name="agent-a")

    assert len(rows) == 1
    assert rows[0]["content"] == "from a"
    store.close()


def test_record_failure_is_contained_and_does_not_raise(tmp_path: Path, caplog):
    """LogStore.record() is called from the middle of a real A2A request's
    hot path — a storage failure here must never propagate and block or
    corrupt delivery of an otherwise successful response to the caller."""
    store = LogStore(tmp_path / "observability.db")
    store.close()  # closing the connection makes the next execute() raise sqlite3.ProgrammingError

    with caplog.at_level("WARNING", logger="agent_runtime.observability"):
        store.record("task-1", "agent-a", "request", "hello")  # must not raise

    assert any("LogStore.record failed" in r.getMessage() for r in caplog.records)


def test_a_second_connection_to_the_same_file_sees_recorded_events(tmp_path: Path):
    """The real deployment shape: the gateway process writes via one LogStore
    instance, and a separate UI process opens its OWN connection to the same
    SQLite file and reads. A same-instance round trip proves nothing about
    that — the real proof is closing one connection and opening a second."""
    db_path = tmp_path / "observability.db"
    writer = LogStore(db_path)
    writer.record("task-1", "agent-a", "request", "hello")
    writer.record("task-1", "agent-a", "response", "hi there")
    writer.close()

    reader = LogStore(db_path)
    events = reader.get_task_events("task-1")

    assert [e["event_type"] for e in events] == ["request", "response"]
    rows = reader.query(agent_name="agent-a")
    assert len(rows) == 2
    reader.close()


def test_record_failure_does_not_leak_content_at_warning_level(tmp_path: Path, caplog):
    sensitive_content = "super-secret-file-contents"
    store = LogStore(tmp_path / "observability.db")
    store.close()

    with caplog.at_level("WARNING", logger="agent_runtime.observability"):
        store.record("task-1", "agent-a", "tool_result", sensitive_content)

    warning_text = "\n".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert sensitive_content not in warning_text
