"""A2A Gateway: exposes one or more loaded agents over the A2A protocol.

A2A itself has no concept of "many agents behind one URL": an Agent Card
describes exactly one agent, reachable at exactly one RPC url, and its
`skills` list is for discovery only — there's no field in message/send to
pick a skill/agent at request time. So multiple markdown agents are hosted
by giving each one its own Agent Card and RPC endpoint under a distinct
sub-path (/agents/<name>/...), all mounted on a single FastAPI app/port.
That keeps deployment to one process and one base URL, while every agent
stays independently, spec-correctly addressable — e.g. as its own
OutSystems ODC external agent connector.
"""

from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import get_message_text, new_agent_text_message, new_task
from a2a.utils.errors import ServerError
from fastapi import FastAPI

from .loader import AgentDefinition
from .runtime import AgentRuntime

logger = logging.getLogger(__name__)


class MarkdownAgentExecutor(AgentExecutor):
    """Bridges the A2A request lifecycle to a single AgentRuntime.run() call."""

    def __init__(self, agent: AgentDefinition, runtime: AgentRuntime):
        self._agent = agent
        self._runtime = runtime

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(TaskState.working)

        user_text = get_message_text(context.message)
        logger.info(
            "Agent '%s': received request (task_id=%s, %d chars)",
            self._agent.name,
            task.id,
            len(user_text),
        )
        logger.debug("Agent '%s': request body (task_id=%s): %r", self._agent.name, task.id, user_text)
        try:
            result = await self._runtime.run(self._agent, user_text)
        except Exception as exc:  # noqa: BLE001 - surfaced to the A2A caller as a failed task
            logger.exception("Agent '%s': request failed (task_id=%s)", self._agent.name, task.id)
            await updater.update_status(
                TaskState.failed,
                message=new_agent_text_message(
                    f"Agent error: {exc}", context_id=task.context_id, task_id=task.id
                ),
            )
            return

        logger.info(
            "Agent '%s': completed request (task_id=%s, tool_calls_made=%d, %d chars)",
            self._agent.name,
            task.id,
            result.tool_calls_made,
            len(result.text),
        )
        logger.debug("Agent '%s': response body (task_id=%s): %r", self._agent.name, task.id, result.text)
        await updater.add_artifact(
            [Part(root=TextPart(text=result.text))],
            name=f"{self._agent.name}-result",
        )
        # Alongside the artifact, also attach a plain agent Message carrying the
        # same text. Some A2A clients (e.g. OutSystems ODC's chat UI) only render
        # a Message inline and treat a bare Task+artifact as fire-and-forget,
        # surfacing just "task in progress, check back later" and never
        # displaying the result even after the task reaches "completed".
        await updater.update_status(
            TaskState.completed,
            message=new_agent_text_message(result.text, context_id=task.context_id, task_id=task.id),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


def build_agent_card(agent: AgentDefinition, public_url: str) -> AgentCard:
    return AgentCard(
        name=agent.name,
        description=agent.description,
        url=public_url,
        version="1.0.0",
        # MarkdownAgentExecutor.execute() already emits its events (new_task,
        # working, artifact, completed) onto the shared EventQueue that
        # a2a-sdk's DefaultRequestHandler.on_message_send_stream() forwards
        # over SSE — no executor change needed to support message/stream.
        # This is task-state-transition streaming, not LLM token streaming:
        # AgentRuntime.run() still returns one full turn at a time (the
        # tool-calling loop only produces user-facing text on its final
        # iteration), so a streaming client sees the same handful of events
        # a polling client would see via tasks/get, just pushed instead of
        # pulled. OutSystems ODC — the primary caller today — renders only
        # inline Messages and doesn't consume message/stream, so this has no
        # effect on ODC; it's for any future client that does.
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id=agent.name,
                name=agent.name,
                description=agent.description,
                tags=["local-llm", "markdown-agent"],
            )
        ],
    )


def build_agent_app(
    agent: AgentDefinition, runtime: AgentRuntime, public_url: str, task_store: TaskStore | None = None
) -> FastAPI:
    """Builds a standalone A2A app for a single agent, reachable at public_url.

    `task_store` defaults to an in-memory store (tasks lost on restart) when
    not given — callers that want a task to survive a gateway restart should
    pass a `SQLiteTaskStore`. Each agent gets its own `DefaultRequestHandler`
    but the same `task_store` instance is safe to share across agents: task
    ids are SDK-generated UUIDs, so collisions across agents aren't a concern.
    """
    agent_card = build_agent_card(agent, public_url)
    executor = MarkdownAgentExecutor(agent, runtime)
    request_handler = DefaultRequestHandler(agent_executor=executor, task_store=task_store or InMemoryTaskStore())
    application = A2AFastAPIApplication(agent_card=agent_card, http_handler=request_handler)
    app = application.build()

    # The A2A RPC endpoint ("/") is POST-only per spec. Some external-agent
    # connectors (e.g. OutSystems ODC's "Test Connection") probe it with a
    # bare GET as a reachability check before ever sending a JSON-RPC call;
    # without this, that GET hits FastAPI's default 405 and the connector
    # reports the agent as unreachable. This route only answers that probe —
    # it carries no A2A semantics of its own.
    @app.get("/", include_in_schema=False)
    async def rpc_endpoint_probe() -> dict[str, str]:
        return {"status": "ok", "agent": agent.name}

    return app


def _agent_base_url(gateway_base_url: str, agent_name: str) -> str:
    return f"{gateway_base_url.rstrip('/')}/agents/{agent_name}/"


def build_gateway_app(
    agents: list[AgentDefinition],
    runtime: AgentRuntime,
    gateway_base_url: str,
    task_store: TaskStore | None = None,
) -> FastAPI:
    """Builds one FastAPI app hosting every agent, each under its own /agents/<name>/ sub-path.

    Each agent gets a fully independent Agent Card and RPC endpoint, mounted
    as its own A2A sub-application, so external callers (e.g. an OutSystems
    ODC external agent connector) address one agent per sub-path while
    operators only run and expose a single process/port. `task_store` is
    shared across every mounted agent (see `build_agent_app`).
    """
    if not agents:
        raise ValueError("No agents to serve: agents_dir contains no matching .md files")

    gateway = FastAPI(title="Agent Runtime Gateway")
    directory: list[dict[str, str]] = []

    for agent in agents:
        agent_url = _agent_base_url(gateway_base_url, agent.name)
        sub_app = build_agent_app(agent, runtime, agent_url, task_store=task_store)
        gateway.mount(f"/agents/{agent.name}", sub_app)
        directory.append(
            {
                "name": agent.name,
                "description": agent.description,
                "agent_card_url": f"{agent_url}.well-known/agent-card.json",
            }
        )

    @gateway.get("/agents")
    async def list_agents() -> dict[str, list[dict[str, str]]]:
        return {"agents": directory}

    return gateway
