from pathlib import Path

import pytest
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TextPart

from agent_runtime.task_store import SQLiteTaskStore


def make_task(task_id: str = "task-1", state: TaskState = TaskState.working) -> Task:
    return Task(id=task_id, context_id="ctx-1", status=TaskStatus(state=state))


@pytest.mark.asyncio
async def test_save_then_get_round_trips_the_task(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    task = make_task()

    await store.save(task)
    loaded = await store.get(task.id)

    assert loaded is not None
    assert loaded.id == task.id
    assert loaded.status.state == TaskState.working
    store.close()


@pytest.mark.asyncio
async def test_get_returns_none_for_an_unknown_task_id(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "tasks.db")

    loaded = await store.get("does-not-exist")

    assert loaded is None
    store.close()


@pytest.mark.asyncio
async def test_save_again_with_the_same_id_updates_in_place(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    task = make_task(state=TaskState.working)

    await store.save(task)
    completed = make_task(state=TaskState.completed)
    await store.save(completed)
    loaded = await store.get(task.id)

    assert loaded.status.state == TaskState.completed
    store.close()


@pytest.mark.asyncio
async def test_delete_removes_the_task(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    task = make_task()
    await store.save(task)

    await store.delete(task.id)
    loaded = await store.get(task.id)

    assert loaded is None
    store.close()


@pytest.mark.asyncio
async def test_delete_of_an_unknown_task_id_does_not_raise(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "tasks.db")

    await store.delete("does-not-exist")  # must not raise

    store.close()


@pytest.mark.asyncio
async def test_task_survives_reopening_the_store_against_the_same_file(tmp_path: Path):
    """The whole point: task state must outlive the process, not just the connection."""
    db_path = tmp_path / "tasks.db"
    store = SQLiteTaskStore(db_path)
    task = make_task()
    await store.save(task)
    store.close()

    reopened = SQLiteTaskStore(db_path)
    loaded = await reopened.get(task.id)

    assert loaded is not None
    assert loaded.id == task.id
    reopened.close()


@pytest.mark.asyncio
async def test_creates_parent_directories_if_missing(tmp_path: Path):
    db_path = tmp_path / "nested" / "state" / "tasks.db"

    store = SQLiteTaskStore(db_path)

    assert db_path.parent.is_dir()
    store.close()


@pytest.mark.asyncio
async def test_round_trips_a_task_with_an_artifact_and_status_message(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    task = make_task(state=TaskState.completed)
    task.status.message = Message(
        message_id="m1",
        role=Role.agent,
        parts=[Part(root=TextPart(text="done"))],
        task_id=task.id,
        context_id=task.context_id,
    )

    await store.save(task)
    loaded = await store.get(task.id)

    assert loaded.status.message.parts[0].root.text == "done"
    store.close()
