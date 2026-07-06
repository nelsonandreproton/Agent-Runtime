"""SQLite-backed activity log for A2A requests, responses, and tool calls.

A separate UI (not built here) needs to browse and filter what each agent did
across time and per-task, so this stores one row per event (request,
tool_call, tool_result, response, error) rather than one row per request,
letting a single task's full sequence be reconstructed in order. Unlike this
project's existing INFO/DEBUG logging split in runtime.py — which never
persists raw content at INFO to avoid leaking secrets into captured stdout —
this store deliberately keeps full unredacted content, since it exists
specifically to let that content be inspected later. It must be treated as a
protected local file, like config/.env, not as a general-purpose log.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    content TEXT NOT NULL
)
"""

_INDEX = "CREATE INDEX IF NOT EXISTS idx_events_agent_time ON events(agent_name, timestamp)"


class LogStore:
    """Persists A2A request/response and tool-call events in a local SQLite file."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.execute(_INDEX)
        self._conn.commit()

    def record(self, task_id: str, agent_name: str, event_type: str, content: str) -> None:
        # This is a best-effort diagnostic sink, called from the middle of a
        # real A2A request's hot path (a2a_gateway.py, runtime.py). A local
        # storage failure here (disk full, file locked, permissions) must
        # never propagate and take down or corrupt delivery of an otherwise
        # successful response to the actual caller — so contain it here,
        # once, rather than requiring every call site to guard itself.
        try:
            self._conn.execute(
                "INSERT INTO events (task_id, agent_name, timestamp, event_type, content) VALUES (?, ?, ?, ?, ?)",
                (task_id, agent_name, datetime.now(UTC).isoformat(), event_type, content),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.warning(
                "LogStore.record failed (task_id=%s, agent_name=%s, event_type=%s)",
                task_id,
                agent_name,
                event_type,
                exc_info=True,
            )

    def query(
        self,
        agent_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses = []
        params: list[str] = []
        if agent_name is not None:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(str(limit))
        rows = self._conn.execute(
            f"SELECT id, task_id, agent_name, timestamp, event_type, content FROM events "
            f"{where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_task_events(self, task_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, task_id, agent_name, timestamp, event_type, content FROM events "
            "WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()


def _row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "task_id": row[1],
        "agent_name": row[2],
        "timestamp": row[3],
        "event_type": row[4],
        "content": row[5],
    }
