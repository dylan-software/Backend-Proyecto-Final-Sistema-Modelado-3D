import httpx
import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.connection import get_session
from app.main import app
from app.models.space import Base, Habitacion, MuebleSugerido
from app.routers.spaces import get_layout_suggester

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_health(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.fixture
async def db_sessions():
    """In-memory SQLite database standing in for PostgreSQL."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    yield sessions
    app.dependency_overrides.pop(get_session, None)
    await engine.dispose()


def room_scan():
    """4 x 3 x 5 m room: six surfaces, a dense existing object near a corner and sparse noise."""
    rng = np.random.default_rng(7)
    n = 300

    def spread(lo, hi):
        return rng.uniform(lo, hi, n)

    def flat(at):
        return at + rng.normal(0, 0.003, n)

    surfaces = [
        np.c_[spread(0, 4), flat(0), spread(0, 5)],  # floor
        np.c_[spread(0, 4), flat(3), spread(0, 5)],  # ceiling
        np.c_[flat(0), spread(0, 3), spread(0, 5)],
        np.c_[flat(4), spread(0, 3), spread(0, 5)],
        np.c_[spread(0, 4), spread(0, 3), flat(0)],
        np.c_[spread(0, 4), spread(0, 3), flat(5)],
    ]
    existing_object = rng.uniform([2.8, 0.1, 3.8], [3.6, 0.8, 4.6], size=(40, 3))
    noise = rng.uniform([0.2, 0.2, 0.2], [3.8, 2.8, 4.8], size=(10, 3))
    # Shift everything: the scanner's coordinate system does not start at the room corner.
    return np.vstack(surfaces + [existing_object, noise]) + [10.0, 0.0, -2.0]


async def fake_suggester(dims, estilo, instrucciones, superficies, contexto_visual=None):
    return {
        "descripcion": f"Propuesta {estilo}",
        "muebles": [
            {"nombre": "mesa", "ancho": 1, "alto": 0.8, "profundidad": 1,
             "posicion_x": 1.5, "posicion_y": 0, "posicion_z": 1.5},
            {"nombre": "armario", "ancho": 1, "alto": 2, "profundidad": 0.6,
             "posicion_x": 3.8, "posicion_y": 0, "posicion_z": 1},  # sticks out of the x = 4 wall
            {"nombre": "sillon", "ancho": 0.6, "alto": 0.6, "profundidad": 0.6,
             "posicion_x": 3.2, "posicion_y": 0.1, "posicion_z": 4.2},  # inside the existing object
            {"nombre": "silla", "ancho": 0.5, "alto": 0.9, "profundidad": 0.5,
             "posicion_x": 1.6, "posicion_y": 0, "posicion_z": 1.6},  # overlaps the table
            {"nombre": "lampara"},  # missing dimensions
        ],
    }


async def test_analyze_full_flow(client, db_sessions):
    app.dependency_overrides[get_layout_suggester] = lambda: fake_suggester
    try:
        response = await client.post(
            "/api/v1/spaces/analyze",
            json={"nombre": "Sala", "estilo": "nordico", "puntos": room_scan().tolist()},
        )
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)

    assert response.status_code == 201, response.text
    body = response.json()

    room = body["habitacion"]
    assert room["ancho"] == pytest.approx(4, abs=0.05)
    assert room["alto"] == pytest.approx(3, abs=0.05)
    assert room["profundidad"] == pytest.approx(5, abs=0.05)
    assert body["origen"] == pytest.approx([10, 0, -2], abs=0.05)
    assert {s["tipo"] for s in body["superficies"]} == {"horizontal", "pared"}
    assert body["descripcion"] == "Propuesta nordico"

    assert [m["nombre"] for m in body["muebles"]] == ["mesa"]
    assert body["descartados"] == [
        {"nombre": "armario", "motivo": "colisión"},
        {"nombre": "sillon", "motivo": "colisión"},
        {"nombre": "silla", "motivo": "colisión"},
        {"nombre": "lampara", "motivo": "datos incompletos"},
    ]

    async with db_sessions() as session:
        saved_room = await session.scalar(select(Habitacion).where(Habitacion.id == room["id"]))
        saved_furniture = (await session.scalars(select(MuebleSugerido))).all()
    assert saved_room.nombre == "Sala"
    assert [(m.nombre, m.habitacion_id) for m in saved_furniture] == [("mesa", room["id"])]


async def test_analyze_reports_omniroute_failure(client, db_sessions):
    from app.services.omniroute import OmniRouteError

    async def failing_suggester(*args, **kwargs):
        raise OmniRouteError("OMNIROUTE_API_KEY is not set")

    app.dependency_overrides[get_layout_suggester] = lambda: failing_suggester
    try:
        response = await client.post(
            "/api/v1/spaces/analyze",
            json={"nombre": "Sala", "puntos": room_scan().tolist()},
        )
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)

    assert response.status_code == 502
    assert "OMNIROUTE_API_KEY" in response.json()["detail"]
