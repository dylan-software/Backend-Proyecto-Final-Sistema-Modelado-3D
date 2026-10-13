import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.database.connection import add_missing_user_columns, engine
from app.geometry.router import router as geometry_router
from app.models.space import Base
from app.routers.auth import router as auth_router
from app.routers.errors import DATABASE_ERRORS
from app.routers.spaces import router as spaces_router
from app.utils.logging_config import configure_logging
from app.utils.middlewares import PROCESS_TIME_HEADER, AuditLoggingMiddleware, TrailingSlashMiddleware

configure_logging()
logger = logging.getLogger(__name__)

CORS_ORIGINS = ["*"]
CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@asynccontextmanager
async def lifespan(app):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await add_missing_user_columns(conn)
    except DATABASE_ERRORS as exc:
        # Keep serving the endpoints that do not need the database.
        logger.warning("Database unreachable at startup, tables were not created: %s", exc)
    yield
    await engine.dispose()


app = FastAPI(title="Sistema de Modelado 3D", lifespan=lifespan)

# Innermost middleware: routes match with or without a trailing slash, without a 307 redirect.
app.add_middleware(TrailingSlashMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # With credentials the wildcard is not valid for browsers, so Starlette echoes the request Origin.
    allow_credentials=True,
    allow_methods=CORS_METHODS,
    allow_headers=["*"],
    # Browsers hide non-standard response headers from cross-origin JavaScript unless they are exposed.
    expose_headers=[PROCESS_TIME_HEADER],
    # Browsers cache the preflight for 10 minutes instead of sending an OPTIONS before every request.
    max_age=600,
)
# Added last, so it is the outermost middleware and its timing covers CORS and the endpoint itself.
app.add_middleware(AuditLoggingMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort handler for errors no endpoint caught: logs the traceback and answers 500.

    The client only gets a generic message and a request_id; the exception text, which may contain
    queries, connection strings or file paths, stays in the server log under that same id.
    """
    request_id = uuid.uuid4().hex[:12]
    logger.exception("request_id=%s Unhandled error in %s %s", request_id, request.method, request.url.path)
    headers = {}
    # This response is built outside CORSMiddleware, so the CORS header has to be set here or the
    # browser hides the body from the frontend and reports a CORS error instead.
    # Credentials are allowed, so the origin is echoed back: browsers reject "*" in that case.
    origin = request.headers.get("origin")
    if origin and ("*" in CORS_ORIGINS or origin in CORS_ORIGINS):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Error interno del servidor.",
            "error": "internal_server_error",
            "request_id": request_id,
        },
        headers=headers,
    )


app.include_router(auth_router)
app.include_router(geometry_router)
app.include_router(spaces_router)


@app.get("/")
async def health():
    """Liveness probe for the frontend and the hosting platform."""
    return {"status": "ok"}
