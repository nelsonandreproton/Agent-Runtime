"""SQLite-backed A2A TaskStore.

`InMemoryTaskStore` (the a2a-sdk default) loses every task on process
restart. Under the current one-shot request/response contract that's mostly
invisible — nothing polls `tasks/get` or resubscribes after a restart — but
it becomes a real gap once a client can reconnect to an in-flight task
(streaming, multi-turn). This gives that data a home across restarts without
adding a new service: one SQLite file, following this project's existing
"SQLite over Postgres for single-process personal-scale services" convention.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types import Task

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
)
"""


class SQLiteTaskStore(TaskStore):
    """Persists A2A Task objects as JSON blobs in a local SQLite file."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: a2a-sdk drives this from an asyncio event
        # loop, not necessarily the thread that opened the connection; each
        # method still runs to completion without yielding mid-statement, so
        # there's no concurrent-access hazard despite the shared connection.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    async def save(self, task: Task, context: ServerCallContext | None = None) -> None:
        self._conn.execute(
            "INSERT INTO tasks (id, data) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (task.id, task.model_dump_json()),
        )
        self._conn.commit()

    async def get(self, task_id: str, context: ServerCallContext | None = None) -> Task | None:
        row = self._conn.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return Task.model_validate_json(row[0])

    async def delete(self, task_id: str, context: ServerCallContext | None = None) -> None:
        self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
