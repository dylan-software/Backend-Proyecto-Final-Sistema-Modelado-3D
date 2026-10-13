import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers hide non-standard response headers from cross-origin JavaScript unless they are exposed.
    expose_headers=[PROCESS_TIME_HEADER],
)
# Added last, so it is the outermost middleware and its timing covers CORS and the endpoint itself.
app.add_middleware(AuditLoggingMiddleware)

app.include_router(auth_router)
app.include_router(geometry_router)
app.include_router(spaces_router)


@app.get("/")
async def health():
    """Liveness probe for the frontend and the hosting platform."""
    return {"status": "ok"}
