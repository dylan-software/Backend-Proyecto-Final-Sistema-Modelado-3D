#!/usr/bin/env bash
# Production entrypoint for Render: starts the FastAPI app with Uvicorn.
set -euo pipefail

# Render routes traffic to $PORT (10000 by default); exec lets Uvicorn receive the shutdown signal.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
