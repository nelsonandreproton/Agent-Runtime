#!/usr/bin/env bash
# Loads config/.env (if present) and starts the A2A gateway for the configured agent.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f config/.env ]; then
  set -a
  # shellcheck disable=SC1091
  source config/.env
  set +a
fi

exec python3 -m agent_runtime.cli
