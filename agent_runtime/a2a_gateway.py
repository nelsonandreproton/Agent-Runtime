"""A2A Gateway: exposes a single loaded agent over the A2A protocol.

Publishes an Agent Card at /.well-known/agent-card.json and a JSON-RPC
endpoint (message/send, tasks/get, ...) that external A2A clients — such as
an OutSystems ODC external agent connector — can call. Each request is
translated into one AgentRuntime.run() call against the loaded markdown
agent.
"""

from __future__ import annotations

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
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
        try:
            result = await self._runtime.run(self._agent, user_text)
        except Exception as exc:  # noqa: BLE001 - surfaced to the A2A caller as a failed task
            await updater.update_status(
                TaskState.failed,
                message=new_agent_text_message(
                    f"Agent error: {exc}", context_id=task.context_id, task_id=task.id
                ),
            )
            return

        await updater.add_artifact(
            [Part(root=TextPart(text=result.text))],
            name=f"{self._agent.name}-result",
        )
        await updater.update_status(TaskState.completed)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


def build_agent_card(agent: AgentDefinition, public_url: str) -> AgentCard:
    return AgentCard(
        name=agent.name,
        description=agent.description,
        url=public_url,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),
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


def build_app(agent: AgentDefinition, runtime: AgentRuntime, public_url: str) -> FastAPI:
    agent_card = build_agent_card(agent, public_url)
    executor = MarkdownAgentExecutor(agent, runtime)
    request_handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    application = A2AFastAPIApplication(agent_card=agent_card, http_handler=request_handler)
    return application.build()
