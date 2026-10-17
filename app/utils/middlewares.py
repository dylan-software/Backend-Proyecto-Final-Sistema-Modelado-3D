"""Global HTTP middlewares of the application."""

import logging
import re
import time

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("app.audit")

# Response header with the server-side processing time in milliseconds, e.g. "X-Process-Time: 12.34".
PROCESS_TIME_HEADER = "X-Process-Time"


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """Audits every HTTP request that reaches the server.

    Logs one line per request: method, path (without the query string, which may carry user data),
    status code and total processing time. 5xx answers, such as a 502 from OmniRoute, are logged as
    warnings so they stand out. The same time is returned to the client in the X-Process-Time header.
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
        response.headers[PROCESS_TIME_HEADER] = f"{request.state.duration_ms:.2f}"
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


class TrailingSlashMiddleware:
    """Serves "/api/v1/spaces/" exactly like "/api/v1/spaces" by dropping the trailing slash.

    FastAPI would answer the slash variant with a 307 redirect instead. Behind the Render proxy that
    redirect can point to http:// and browsers do not follow redirects on CORS preflights, so the
    frontend ends up with a 404 or a CORS error. Rewriting the path before routing avoids the redirect.
    The root path "/" is left untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if len(path) > 1 and path.endswith("/"):
                scope = dict(scope)
                scope["path"] = path.rstrip("/") or "/"
                raw_path = scope.get("raw_path")
                if raw_path:
                    scope["raw_path"] = raw_path.rstrip(b"/") or b"/"
        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Answers 413 when the body of a request to a matching path is larger than max_bytes.

    It runs before routing, so an oversized upload is refused before FastAPI parses it (FastAPI parses
    multipart bodies before running the endpoint's dependencies, authentication included). A declared
    Content-Length above the limit is refused without reading anything. Without one (chunked transfer
    encoding) the body is read and counted as it arrives, and the request is refused as soon as it
    passes the limit; below the limit it is handed to the application unchanged. The whole body is
    held in memory, so this is only meant for small limits on a few paths.
    """

    def __init__(self, app, max_bytes, path_pattern):
        self.app = app
        self.max_bytes = max_bytes
        self.path = re.compile(path_pattern)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.path.match(scope["path"]):
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._too_large(scope, receive, send)
            return

        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # the client left; there is nobody to answer
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._too_large(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope, receive, send):
        limit_mb = self.max_bytes / (1024 * 1024)
        response = JSONResponse(
            status_code=413,
            content={"detail": f"El cuerpo de la petición supera el máximo de {limit_mb:.2f} MB."},
        )
        await response(scope, receive, send)
