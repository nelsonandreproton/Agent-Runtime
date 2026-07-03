#!/usr/bin/env bash
# Starts llama.cpp's llama-server with an OpenAI-compatible API and Jinja-based
# tool-calling enabled. Requires a llama.cpp build with `llama-server` on PATH
# and a chat model that supports tool calling (e.g. Qwen2.5-Instruct,
# Llama-3.1-Instruct, Hermes-2-Pro) in GGUF format.
set -euo pipefail

MODEL_PATH="${1:?Usage: run_llama_server.sh /path/to/model.gguf [extra llama-server args...]}"
shift || true

exec llama-server \
  --model "$MODEL_PATH" \
  --host 127.0.0.1 \
  --port 8080 \
  --jinja \
  --ctx-size 8192 \
  "$@"
