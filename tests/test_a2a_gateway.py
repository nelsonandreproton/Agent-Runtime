from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_runtime.a2a_gateway import build_app
from agent_runtime.loader import AgentDefinition
from agent_runtime.runtime import RuntimeResult


class StubRuntime:
    def __init__(self, text: str = "the review found no issues"):
        self._text = text
        self.calls: list[tuple[AgentDefinition, str]] = []

    async def run(self, agent, user_message):
        self.calls.append((agent, user_message))
        return RuntimeResult(text=self._text, tool_calls_made=0)


def make_agent() -> AgentDefinition:
    return AgentDefinition(
        name="code-reviewer",
        description="Reviews code for issues",
        system_prompt="You are a reviewer.",
        tools=["Read"],
        model=None,
        source_path=Path("agents/code-reviewer.md"),
    )


def make_client(runtime) -> TestClient:
    app = build_app(make_agent(), runtime, public_url="http://testserver/")
    return TestClient(app)


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

    payload = {
        "id": "1",
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "messageId": "m1",
                "role": "user",
                "parts": [{"kind": "text", "text": "review auth.py"}],
            }
        },
    }

    response = client.post("/", json=payload)

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

    payload = {
        "id": "1",
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "messageId": "m1",
                "role": "user",
                "parts": [{"kind": "text", "text": "review auth.py"}],
            }
        },
    }

    response = client.post("/", json=payload)

    assert response.status_code == 200
    task = response.json()["result"]
    assert task["status"]["state"] == "failed"
    assert "llama-server unreachable" in task["status"]["message"]["parts"][0]["text"]
