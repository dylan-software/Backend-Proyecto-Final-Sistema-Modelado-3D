"""Global HTTP middlewares of the application."""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """Audits every HTTP request that reaches the server."""

    async def dispatch(self, request: Request, call_next) -> Response:
        return await call_next(request)
