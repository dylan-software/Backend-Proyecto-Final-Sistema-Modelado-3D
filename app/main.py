from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.geometry.router import router as geometry_router
from app.routers.spaces import router as spaces_router

app = FastAPI(title="Sistema de Modelado 3D")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(geometry_router)
app.include_router(spaces_router)


@app.get("/")
async def health():
    return {"status": "ok"}
