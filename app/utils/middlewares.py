"""Global HTTP middlewares of the application."""

import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """Audits every HTTP request that reaches the server."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # perf_counter is monotonic: unlike time.time() it never jumps when the system clock is adjusted.
        request.state.start_time = time.perf_counter()
        return await call_next(request)
