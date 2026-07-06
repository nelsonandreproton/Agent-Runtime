"""FastAPI routes for the Logs tab of the local admin UI.

Read-only view over agent_runtime.observability.LogStore: browse and filter
recorded A2A request/response/tool-call events, plus a per-task timeline. No
writes happen here — the LogStore is populated elsewhere (a2a_gateway.py),
this module only queries it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from agent_runtime.observability import LogStore


def normalize_end_filter(end: str | None) -> str | None:
    """Pads a bare `datetime-local` end value (no seconds, e.g. "2026-07-06T14:30")
    to the end of that minute with a full ISO timestamp, so it works correctly as
    an inclusive upper bound (`timestamp <= end`) against full
    `datetime.now(UTC).isoformat()` strings stored by LogStore.

    Without this, "2026-07-06T14:30" is a *prefix* of
    "2026-07-06T14:30:45.123456+00:00", and Python string comparison sorts a
    prefix before the longer string it's a prefix of — so the bare value would
    incorrectly exclude every event in that same minute. Values that already
    include seconds are passed through unchanged.
    """
    if end is None:
        return None
    if end.count(":") >= 2:
        return end
    return f"{end}:59.999999+00:00"


def build_logs_router(log_store: LogStore | None, agent_names: list[str], templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/ui/logs")

    @router.get("")
    def list_logs(request: Request, agent: str = "", start: str = "", end: str = "", limit: int = 200):
        if log_store is None:
            return templates.TemplateResponse(
                request,
                "logs.html",
                {"active_tab": "logs", "log_store_disabled": True},
            )

        rows = log_store.query(
            agent_name=agent or None,
            start=start or None,
            end=normalize_end_filter(end or None),
            limit=limit,
        )
        return templates.TemplateResponse(
            request,
            "logs.html",
            {
                "active_tab": "logs",
                "log_store_disabled": False,
                "rows": rows,
                "agent_names": agent_names,
                "selected_agent": agent,
                "start": start,
                "end": end,
                "limit": limit,
            },
        )

    @router.get("/{task_id}")
    def task_detail(request: Request, task_id: str):
        if log_store is None:
            return templates.TemplateResponse(
                request,
                "logs_task.html",
                {"active_tab": "logs", "log_store_disabled": True, "task_id": task_id},
            )

        events = log_store.get_task_events(task_id)
        return templates.TemplateResponse(
            request,
            "logs_task.html",
            {
                "active_tab": "logs",
                "log_store_disabled": False,
                "task_id": task_id,
                "events": events,
            },
        )

    return router
