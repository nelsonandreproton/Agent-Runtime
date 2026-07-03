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


def build_agent_app(agent: AgentDefinition, runtime: AgentRuntime, public_url: str) -> FastAPI:
    """Builds a standalone A2A app for a single agent, reachable at public_url."""
    agent_card = build_agent_card(agent, public_url)
    executor = MarkdownAgentExecutor(agent, runtime)
    request_handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    application = A2AFastAPIApplication(agent_card=agent_card, http_handler=request_handler)
    return application.build()


def _agent_base_url(gateway_base_url: str, agent_name: str) -> str:
    return f"{gateway_base_url.rstrip('/')}/agents/{agent_name}/"


def build_gateway_app(agents: list[AgentDefinition], runtime: AgentRuntime, gateway_base_url: str) -> FastAPI:
    """Builds one FastAPI app hosting every agent, each under its own /agents/<name>/ sub-path.

    Each agent gets a fully independent Agent Card and RPC endpoint, mounted
    as its own A2A sub-application, so external callers (e.g. an OutSystems
    ODC external agent connector) address one agent per sub-path while
    operators only run and expose a single process/port.
    """
    if not agents:
        raise ValueError("No agents to serve: agents_dir contains no matching .md files")

    gateway = FastAPI(title="Agent Runtime Gateway")
    directory: list[dict[str, str]] = []

    for agent in agents:
        agent_url = _agent_base_url(gateway_base_url, agent.name)
        sub_app = build_agent_app(agent, runtime, agent_url)
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
