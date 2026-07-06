from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_runtime.a2a_gateway import build_agent_app, build_gateway_app
from agent_runtime.loader import AgentDefinition
from agent_runtime.runtime import RuntimeResult
from agent_runtime.task_store import SQLiteTaskStore


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
    assert card["capabilities"]["streaming"] is True


def message_stream_payload(text: str) -> dict:
    return {
        "id": "1",
        "jsonrpc": "2.0",
        "method": "message/stream",
        "params": {
            "message": {
                "kind": "message",
                "messageId": "m1",
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


def read_sse_events(client, payload: dict) -> list[dict]:
    import json

    events = []
    with client.stream("POST", "/", json=payload, headers={"Accept": "text/event-stream"}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def test_message_stream_pushes_working_then_artifact_then_completed_events():
    client = make_client(StubRuntime(text="found one bug in auth.py"))

    events = read_sse_events(client, message_stream_payload("review auth.py"))

    kinds = [e["result"]["kind"] for e in events]
    assert kinds == ["task", "status-update", "artifact-update", "status-update"]
    assert events[1]["result"]["status"]["state"] == "working"
    assert events[2]["result"]["artifact"]["parts"][0]["text"] == "found one bug in auth.py"
    final_status = events[3]["result"]["status"]
    assert final_status["state"] == "completed"
    # same ODC-facing guarantee as the non-streaming path: the terminal event
    # carries an inline Message, not just a bare artifact.
    assert final_status["message"]["parts"][0]["text"] == "found one bug in auth.py"


def test_message_stream_surfaces_a_failed_task_as_a_terminal_event():
    class FailingRuntime:
        async def run(self, agent, user_message):
            raise RuntimeError("llama-server unreachable")

    client = make_client(FailingRuntime())

    events = read_sse_events(client, message_stream_payload("review auth.py"))

    final_status = events[-1]["result"]["status"]
    assert final_status["state"] == "failed"
    assert "llama-server unreachable" in final_status["message"]["parts"][0]["text"]


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
    # A2A clients that only render inline Messages (e.g. OutSystems ODC's chat
    # UI) rely on this — a bare artifact with no status message is invisible to them.
    assert task["status"]["message"]["parts"][0]["text"] == "found one bug in auth.py"
    assert task["status"]["message"]["role"] == "agent"

    # the runtime received the plain-text user message extracted from the A2A message
    assert runtime.calls[0][1] == "review auth.py"


def test_rpc_endpoint_answers_a_bare_get_for_connector_reachability_probes():
    client = make_client(StubRuntime())

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "agent": "code-reviewer"}

    # must not appear in the public OpenAPI schema (it carries no A2A semantics)
    schema = client.get("/openapi.json").json()
    get_ops = schema["paths"].get("/", {})
    assert "get" not in get_ops


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


def tasks_get_payload(task_id: str) -> dict:
    return {"id": "2", "jsonrpc": "2.0", "method": "tasks/get", "params": {"id": task_id}}


def test_a_sqlite_task_store_is_actually_used_when_provided(tmp_path: Path):
    """Proves the task_store parameter is wired through, not just accepted and ignored."""
    task_store = SQLiteTaskStore(tmp_path / "tasks.db")
    app = build_agent_app(make_agent(), StubRuntime("done"), public_url="http://testserver/", task_store=task_store)
    client = TestClient(app)

    send_response = client.post("/", json=send_message_payload("review auth.py"))
    task_id = send_response.json()["result"]["id"]

    get_response = client.post("/", json=tasks_get_payload(task_id))

    assert get_response.json()["result"]["id"] == task_id
    assert get_response.json()["result"]["status"]["state"] == "completed"
    task_store.close()


def test_a_task_persisted_to_sqlite_is_queryable_after_the_store_is_reopened(tmp_path: Path):
    """The actual point of SQLiteTaskStore: a task must survive a process restart,
    simulated here by closing the store and reopening it against the same file."""
    db_path = tmp_path / "tasks.db"
    task_store = SQLiteTaskStore(db_path)
    app = build_agent_app(make_agent(), StubRuntime("done"), public_url="http://testserver/", task_store=task_store)
    client = TestClient(app)
    task_id = client.post("/", json=send_message_payload("review auth.py")).json()["result"]["id"]
    task_store.close()

    reopened_store = SQLiteTaskStore(db_path)
    reopened_app = build_agent_app(
        make_agent(), StubRuntime("done"), public_url="http://testserver/", task_store=reopened_store
    )
    reopened_client = TestClient(reopened_app)

    get_response = reopened_client.post("/", json=tasks_get_payload(task_id))

    assert get_response.json()["result"]["id"] == task_id
    reopened_store.close()


def test_gateway_shares_one_task_store_across_every_mounted_agent(tmp_path: Path):
    """A same-agent get proves nothing about sharing — it would pass identically
    if each agent got its own independent store. The real proof is a
    *cross*-agent lookup: a task created via agent-a's endpoint must be
    readable through agent-b's endpoint, which only holds if both sub-apps
    were wired to the same underlying TaskStore instance."""
    task_store = SQLiteTaskStore(tmp_path / "tasks.db")
    runtime = RoutingStubRuntime()
    app = build_gateway_app(
        [make_agent("agent-a", "Agent A"), make_agent("agent-b", "Agent B")],
        runtime,
        gateway_base_url="http://testserver/",
        task_store=task_store,
    )
    client = TestClient(app)

    task_id_a = client.post("/agents/agent-a/", json=send_message_payload("hi a")).json()["result"]["id"]
    task_id_b = client.post("/agents/agent-b/", json=send_message_payload("hi b")).json()["result"]["id"]

    cross_get_a_via_b = client.post("/agents/agent-b/", json=tasks_get_payload(task_id_a))
    cross_get_b_via_a = client.post("/agents/agent-a/", json=tasks_get_payload(task_id_b))

    assert cross_get_a_via_b.json()["result"]["id"] == task_id_a
    assert cross_get_b_via_a.json()["result"]["id"] == task_id_b
    task_store.close()
