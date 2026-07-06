import json

import httpx
import pytest

from agent_runtime import llm_client as llm_client_module
from agent_runtime.llm_client import LlamaServerClient, LlamaServerError

_RealAsyncClient = httpx.AsyncClient


def patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    monkeypatch.setattr(
        llm_client_module.httpx,
        "AsyncClient",
        lambda **kwargs: _RealAsyncClient(transport=transport),
    )


def make_transport(capture: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        capture["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_chat_uses_the_client_default_model_when_no_override_given(monkeypatch):
    capture: dict = {}
    patch_async_client(monkeypatch, make_transport(capture))

    client = LlamaServerClient(base_url="http://127.0.0.1:8080", model="default-model")
    await client.chat([{"role": "user", "content": "hi"}])

    assert capture["payload"]["model"] == "default-model"


@pytest.mark.asyncio
async def test_chat_uses_the_per_call_model_override_when_given(monkeypatch):
    capture: dict = {}
    patch_async_client(monkeypatch, make_transport(capture))

    client = LlamaServerClient(base_url="http://127.0.0.1:8080", model="default-model")
    await client.chat([{"role": "user", "content": "hi"}], model="agent-specific-model")

    assert capture["payload"]["model"] == "agent-specific-model"


@pytest.mark.asyncio
async def test_unexpected_response_shape_raises_llama_server_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    patch_async_client(monkeypatch, httpx.MockTransport(handler))

    client = LlamaServerClient(base_url="http://127.0.0.1:8080", model="default-model")

    with pytest.raises(LlamaServerError, match="Unexpected llama-server response shape"):
        await client.chat([{"role": "user", "content": "hi"}])
