#!/usr/bin/env bash
# Loads config/.env (if present) and starts the local admin UI (Agents, MCP
# Servers, Logs tabs). Binds to 127.0.0.1 only, hardcoded in ui_cli.py -- this
# must never be exposed via ngrok/Cloudflare Tunnel/any public tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f config/.env ]; then
  set -a
  # shellcheck disable=SC1091
  source config/.env
  set +a
fi

exec python3 -m agent_runtime.ui_cli
