import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database.connection import add_missing_room_columns, add_missing_user_columns, engine
from app.geometry.router import router as geometry_router
from app.models.space import Base
from app.routers import auth, chat, rooms
from app.routers.errors import DATABASE_ERRORS
from app.routers.spaces import MAX_PHOTO_REQUEST_BYTES, PHOTO_UPLOAD_PATH, debug_router
from app.routers.spaces import router as spaces_router
from app.utils.logging_config import configure_logging
from app.utils.middlewares import (
    PROCESS_TIME_HEADER,
    AuditLoggingMiddleware,
    BodySizeLimitMiddleware,
    TrailingSlashMiddleware,
)

configure_logging()
logger = logging.getLogger(__name__)

CORS_ORIGINS = get_settings().cors_origins
CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@asynccontextmanager
async def lifespan(app):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await add_missing_user_columns(conn)
            await add_missing_room_columns(conn)
    except DATABASE_ERRORS as exc:
        # Keep serving the endpoints that do not need the database.
        logger.warning("Database unreachable at startup, tables were not created: %s", exc)
    yield
    await engine.dispose()


app = FastAPI(title="Sistema de Modelado 3D", lifespan=lifespan)

# Innermost middleware: routes match with or without a trailing slash, without a 307 redirect.
app.add_middleware(TrailingSlashMiddleware)
# Photo uploads are refused before FastAPI parses them (and before authentication) when too large.
# Inside CORSMiddleware, so the browser can read the 413.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_PHOTO_REQUEST_BYTES, path_pattern=PHOTO_UPLOAD_PATH)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # Sessions travel in the Authorization header, not in cookies, so credentials stay off.
    allow_credentials=False,
    allow_methods=CORS_METHODS,
    allow_headers=["Authorization", "Content-Type"],
    # Browsers hide non-standard response headers from cross-origin JavaScript unless they are exposed.
    expose_headers=[PROCESS_TIME_HEADER],
    # Browsers cache the preflight for 10 minutes instead of sending an OPTIONS before every request.
    max_age=600,
)
# Added last, so it is the outermost middleware and its timing covers CORS and the endpoint itself.
app.add_middleware(AuditLoggingMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 for an invalid request, listing only the type, location and message of each error.

    FastAPI's default handler echoes each error's "input" (and "ctx") back to the client. When the body
    is not valid UTF-8 and not JSON, that input is raw bytes, encoding it raises UnicodeDecodeError and
    the client gets a 500 instead of a 422. Leaving both fields out also avoids reflecting the request.
    """
    errors = [
        {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]} for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


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
    # Only allowed origins are echoed back, exactly as CORSMiddleware does.
    origin = request.headers.get("origin")
    if origin and origin in CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
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


app.include_router(auth.router, prefix="/api/v1/auth")
app.include_router(geometry_router)
app.include_router(spaces_router)
app.include_router(rooms.router)
app.include_router(chat.router)
if get_settings().enable_debug_routes:
    app.include_router(debug_router)


@app.get("/")
async def health():
    """Liveness probe for the frontend and the hosting platform."""
    return {"status": "ok"}


@app.get("/api/v1/health")
async def database_health():
    """Readiness probe: runs a trivial query to confirm the live connection to Supabase.

    Answers 503 when the database is unreachable, so monitors can tell "process up" from "service usable".
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except DATABASE_ERRORS as exc:
        logger.warning("Health check could not reach the database: %s", exc)
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "offline"})
    return {"status": "online", "database": "online"}
