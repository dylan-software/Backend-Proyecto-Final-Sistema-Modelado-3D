#!/usr/bin/env bash
# Production entrypoint for Render: starts the FastAPI app with Uvicorn.
set -euo pipefail

# Render routes traffic to $PORT (10000 by default); exec lets Uvicorn receive the shutdown signal.
# --no-proxy-headers: uvicorn must not rewrite the client IP from X-Forwarded-For; TRUSTED_PROXY_HOPS decides it.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}" --no-proxy-headers
