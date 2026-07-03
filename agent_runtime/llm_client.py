"""Thin async client for llama.cpp's llama-server, via its OpenAI-compatible API."""

from __future__ import annotations

from typing import Any

import httpx


class LlamaServerError(RuntimeError):
    pass


class LlamaServerClient:
    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Sends a chat completion request and returns the assistant message dict.

        The returned dict follows the OpenAI chat message shape: {"role": "assistant",
        "content": str | None, "tool_calls": [...] | absent}.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(f"{self._base_url}/v1/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                raise LlamaServerError(f"Could not reach llama-server at {self._base_url}: {exc}") from exc

        if response.status_code != 200:
            raise LlamaServerError(f"llama-server returned {response.status_code}: {response.text}")

        data = response.json()
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LlamaServerError(f"Unexpected llama-server response shape: {data}") from exc
