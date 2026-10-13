"""Global HTTP middlewares of the application."""

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("app.audit")


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """Audits every HTTP request that reaches the server.

    Logs one line per request: method, path (without the query string, which may carry user data),
    status code and total processing time. 5xx answers, such as a 502 from OmniRoute, are logged as
    warnings so they stand out.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # perf_counter is monotonic: unlike time.time() it never jumps when the system clock is adjusted.
        request.state.start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Unhandled error: the global exception handler answers 500; record it here with its timing.
            self._log(request, 500, logging.ERROR)
            raise
        self._log(
            request, response.status_code, logging.WARNING if response.status_code >= 500 else logging.INFO
        )
        return response

    @staticmethod
    def _log(request, status_code, level):
        request.state.duration_ms = (time.perf_counter() - request.state.start_time) * 1000
        logger.log(
            level,
            "method=%s path=%s status=%d duration_ms=%.2f",
            request.method,
            request.url.path,
            status_code,
            request.state.duration_ms,
        )
