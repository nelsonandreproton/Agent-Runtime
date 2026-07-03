from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_runtime.a2a_gateway import build_agent_app, build_gateway_app
from agent_runtime.loader import AgentDefinition
from agent_runtime.runtime import RuntimeResult


class StubRuntime:
    def __init__(self, text: str = "the review found no issues"):
        self._text = text
        self.calls: list[tuple[AgentDefinition, str]] = []

    async def run(self, agent, user_message):
        self.calls.append((agent, user_message))
        return RuntimeResult(text=self._text, tool_calls_made=0)


class RoutingStubRuntime:
    """Returns a response naming which agent handled the call, to prove routing is correct."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def run(self, agent, user_message):
        self.calls.append((agent.name, user_message))
        return RuntimeResult(text=f"handled by {agent.name}", tool_calls_made=0)


def make_agent(name: str = "code-reviewer", description: str = "Reviews code for issues") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        description=description,
        system_prompt="You are a reviewer.",
        tools=["Read"],
        model=None,
        source_path=Path(f"agents/{name}.md"),
    )


def make_client(runtime) -> TestClient:
    app = build_agent_app(make_agent(), runtime, public_url="http://testserver/")
    return TestClient(app)


def send_message_payload(text: str) -> dict:
    return {
        "id": "1",
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "messageId": "m1",
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


def test_agent_card_is_published_at_the_well_known_path():
    client = make_client(StubRuntime())

    response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "code-reviewer"
    assert card["url"] == "http://testserver/"
    assert card["skills"][0]["id"] == "code-reviewer"


def test_message_send_runs_the_agent_and_returns_a_completed_task():
    runtime = StubRuntime(text="found one bug in auth.py")
    client = make_client(runtime)

    response = client.post("/", json=send_message_payload("review auth.py"))

    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    task = body["result"]
    assert task["status"]["state"] == "completed"
    artifact_text = task["artifacts"][0]["parts"][0]["text"]
    assert artifact_text == "found one bug in auth.py"

    # the runtime received the plain-text user message extracted from the A2A message
    assert runtime.calls[0][1] == "review auth.py"


def test_agent_error_surfaces_as_a_failed_task():
    class FailingRuntime:
        async def run(self, agent, user_message):
            raise RuntimeError("llama-server unreachable")

    client = make_client(FailingRuntime())

    response = client.post("/", json=send_message_payload("review auth.py"))

    assert response.status_code == 200
    task = response.json()["result"]
    assert task["status"]["state"] == "failed"
    assert "llama-server unreachable" in task["status"]["message"]["parts"][0]["text"]


def test_gateway_rejects_an_empty_agent_list():
    with pytest.raises(ValueError, match="No agents to serve"):
        build_gateway_app([], StubRuntime(), gateway_base_url="http://testserver/")


def test_gateway_lists_every_mounted_agent():
    runtime = RoutingStubRuntime()
    app = build_gateway_app(
        [make_agent("agent-a", "Agent A"), make_agent("agent-b", "Agent B")],
        runtime,
        gateway_base_url="http://testserver/",
    )
    client = TestClient(app)

    response = client.get("/agents")

    assert response.status_code == 200
    directory = {entry["name"]: entry for entry in response.json()["agents"]}
    assert directory.keys() == {"agent-a", "agent-b"}
    assert directory["agent-a"]["agent_card_url"] == "http://testserver/agents/agent-a/.well-known/agent-card.json"
    assert directory["agent-b"]["agent_card_url"] == "http://testserver/agents/agent-b/.well-known/agent-card.json"


def test_gateway_serves_each_agent_card_under_its_own_subpath():
    runtime = RoutingStubRuntime()
    app = build_gateway_app(
        [make_agent("agent-a", "Agent A"), make_agent("agent-b", "Agent B")],
        runtime,
        gateway_base_url="http://testserver/",
    )
    client = TestClient(app)

    card_a = client.get("/agents/agent-a/.well-known/agent-card.json").json()
    card_b = client.get("/agents/agent-b/.well-known/agent-card.json").json()

    assert card_a["name"] == "agent-a"
    assert card_a["url"] == "http://testserver/agents/agent-a/"
    assert card_b["name"] == "agent-b"
    assert card_b["url"] == "http://testserver/agents/agent-b/"


def test_gateway_routes_message_send_to_the_matching_agent():
    runtime = RoutingStubRuntime()
    app = build_gateway_app(
        [make_agent("agent-a", "Agent A"), make_agent("agent-b", "Agent B")],
        runtime,
        gateway_base_url="http://testserver/",
    )
    client = TestClient(app)

    response_a = client.post("/agents/agent-a/", json=send_message_payload("hi a"))
    response_b = client.post("/agents/agent-b/", json=send_message_payload("hi b"))

    assert response_a.json()["result"]["artifacts"][0]["parts"][0]["text"] == "handled by agent-a"
    assert response_b.json()["result"]["artifacts"][0]["parts"][0]["text"] == "handled by agent-b"
    assert runtime.calls == [("agent-a", "hi a"), ("agent-b", "hi b")]
