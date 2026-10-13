import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import engine
from app.geometry.router import router as geometry_router
from app.models.space import Base
from app.routers.auth import router as auth_router
from app.routers.spaces import router as spaces_router
from app.utils.logging_config import configure_logging
from app.utils.middlewares import PROCESS_TIME_HEADER, AuditLoggingMiddleware

configure_logging()
logger = logging.getLogger(__name__)

CORS_ORIGINS = ["*"]


@asynccontextmanager
async def lifespan(app):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except (SQLAlchemyError, OSError) as exc:
        # Keep serving the endpoints that do not need the database.
        logger.warning("Database unreachable at startup, tables were not created: %s", exc)
    yield
    await engine.dispose()


app = FastAPI(title="Sistema de Modelado 3D", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers hide non-standard response headers from cross-origin JavaScript unless they are exposed.
    expose_headers=[PROCESS_TIME_HEADER],
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
    origin = request.headers.get("origin")
    if "*" in CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = "*"
    elif origin in CORS_ORIGINS:
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


app.include_router(auth_router)
app.include_router(geometry_router)
app.include_router(spaces_router)


@app.get("/")
async def health():
    """Liveness probe for the frontend and the hosting platform."""
    return {"status": "ok"}
