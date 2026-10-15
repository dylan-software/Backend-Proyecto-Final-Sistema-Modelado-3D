import base64

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app.main import app
from app.models.space import Categoria, Habitacion, MuebleCatalogo, MuebleSugerido
from app.routers.spaces import get_coherence_validator, get_layout_suggester
from app.services import semantic_filter
from app.services.omniroute import OmniRouteError

pytestmark = pytest.mark.anyio


async def test_health(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
            {
                "nombre": "mesa",
                "ancho": 1,
                "alto": 0.8,
                "profundidad": 1,
                "posicion_x": 1.5,
                "posicion_y": 0,
                "posicion_z": 1.5,
            },
            {
                "nombre": "armario",
                "ancho": 1,
                "alto": 2,
                "profundidad": 0.6,
                "posicion_x": 3.8,
                "posicion_y": 0,
                "posicion_z": 1,
            },  # sticks out of the x = 4 wall
            {
                "nombre": "sillon",
                "ancho": 0.6,
                "alto": 0.6,
                "profundidad": 0.6,
                "posicion_x": 3.2,
                "posicion_y": 0.1,
                "posicion_z": 4.2,
            },  # inside the existing object
            {
                "nombre": "silla",
                "ancho": 0.5,
                "alto": 0.9,
                "profundidad": 0.5,
                "posicion_x": 1.6,
                "posicion_y": 0,
                "posicion_z": 1.6,
            },  # overlaps the table
            {"nombre": "lampara"},  # missing dimensions
        ],
    }


async def test_analyze_full_flow(user_a, db_sessions):
    app.dependency_overrides[get_layout_suggester] = lambda: fake_suggester
    try:
        response = await user_a.post(
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


async def test_analyze_reports_omniroute_failure(user_a, db_sessions):
    from app.services.omniroute import OmniRouteError

    async def failing_suggester(*args, **kwargs):
        raise OmniRouteError("OMNIROUTE_API_KEY is not set")

    app.dependency_overrides[get_layout_suggester] = lambda: failing_suggester
    try:
        response = await user_a.post(
            "/api/v1/spaces/analyze",
            json={"nombre": "Sala", "puntos": room_scan().tolist()},
        )
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)

    assert response.status_code == 502
    assert "OMNIROUTE_API_KEY" in response.json()["detail"]


def encoded_photo(bgr, width=64, height=48):
    """PNG data URL: three quarters in the given color, the left quarter white."""
    image = np.zeros((height, width, 3), np.uint8)
    image[:] = bgr
    image[:, : width // 4] = (255, 255, 255)
    ok, png = cv2.imencode(".png", image)
    assert ok
    return "data:image/png;base64," + base64.b64encode(png.tobytes()).decode()


async def test_analyze_reads_room_photo(user_a, db_sessions):
    received = {}

    async def capturing_suggester(dims, estilo, instrucciones, superficies, contexto_visual=None):
        received["contexto_visual"] = contexto_visual
        return {"descripcion": "", "muebles": []}

    app.dependency_overrides[get_layout_suggester] = lambda: capturing_suggester
    try:
        response = await user_a.post(
            "/api/v1/spaces/analyze",
            json={"nombre": "Sala", "puntos": room_scan().tolist(), "imagen": encoded_photo((0, 0, 200))},
        )
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)

    assert response.status_code == 201, response.text
    visual = response.json()["contexto_visual"]
    assert [c["hex"] for c in visual["colores"]] == ["#c80000", "#ffffff"]
    assert [c["proporcion"] for c in visual["colores"]] == [0.75, 0.25]
    assert received["contexto_visual"] == visual


async def test_analyze_rejects_invalid_photo(user_a, db_sessions):
    app.dependency_overrides[get_layout_suggester] = lambda: fake_suggester
    try:
        response = await user_a.post(
            "/api/v1/spaces/analyze",
            json={"nombre": "Sala", "puntos": room_scan().tolist(), "imagen": "esto no es una imagen"},
        )
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)

    assert response.status_code == 422
    assert "Imagen inválida" in response.json()["detail"]


COHERENT_NAMES = {"silla", "puff"}


@pytest.fixture
def fake_validator():
    """Stand-in for OmniRoute: only the names in COHERENT_NAMES are furniture. Records every call."""
    calls = []

    async def validator(nombre):
        calls.append(nombre)
        return nombre.strip().lower() in COHERENT_NAMES

    app.dependency_overrides[get_coherence_validator] = lambda: validator
    yield calls
    app.dependency_overrides.pop(get_coherence_validator, None)


def catalog_item(nombre, categoria="Sillas", custom_category=None):
    return {
        "nombre": nombre,
        "categoria": categoria,
        "custom_category": custom_category,
        "ancho": 0.5,
        "alto": 0.9,
        "profundidad": 0.5,
    }


async def test_catalog_accepts_coherent_furniture(user_a, db_sessions, fake_validator):
    response = await user_a.post("/api/v1/spaces/catalog", json=catalog_item("Silla"))

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["nombre"], body["categoria"], body["categoria_nueva"]) == ("Silla", "Sillas", True)
    assert fake_validator == ["Silla"]
    async with db_sessions() as session:
        saved = (await session.scalars(select(MuebleCatalogo))).all()
    assert [(m.nombre, m.categoria_id) for m in saved] == [("Silla", body["categoria_id"])]


async def test_catalog_rejects_incoherent_furniture(user_a, db_sessions, fake_validator):
    response = await user_a.post(
        "/api/v1/spaces/catalog", json=catalog_item("Algoritmo", "Others", custom_category="Ideas")
    )

    assert response.status_code == 422
    assert "Algoritmo" in response.json()["detail"]
    # Nothing is stored, not even the new category.
    async with db_sessions() as session:
        assert (await session.scalars(select(MuebleCatalogo))).all() == []
        assert (await session.scalars(select(Categoria))).all() == []


async def test_catalog_creates_custom_category_once(user_a, db_sessions, fake_validator):
    first = await user_a.post(
        "/api/v1/spaces/catalog", json=catalog_item("Puff", "Others", custom_category="Asientos bajos")
    )
    second = await user_a.post(
        "/api/v1/spaces/catalog", json=catalog_item("Silla", "others", custom_category="  asientos   BAJOS ")
    )

    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()["categoria_nueva"] is True
    assert second.json()["categoria_nueva"] is False
    assert second.json()["categoria_id"] == first.json()["categoria_id"]
    async with db_sessions() as session:
        assert [c.nombre for c in (await session.scalars(select(Categoria))).all()] == ["Asientos bajos"]


async def test_catalog_requires_custom_category_for_others(user_a, db_sessions, fake_validator):
    response = await user_a.post("/api/v1/spaces/catalog", json=catalog_item("Puff", "Others"))

    assert response.status_code == 422
    assert "custom_category" in response.json()["detail"]


async def test_catalog_reports_filter_failure(user_a, db_sessions):
    async def failing_validator(nombre):
        raise OmniRouteError("request failed: timeout")

    app.dependency_overrides[get_coherence_validator] = lambda: failing_validator
    try:
        response = await user_a.post("/api/v1/spaces/catalog", json=catalog_item("Silla"))
    finally:
        app.dependency_overrides.pop(get_coherence_validator, None)

    assert response.status_code == 502
    async with db_sessions() as session:
        assert (await session.scalars(select(MuebleCatalogo))).all() == []


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("TRUE", True),
        (" false\n", False),
        ("```TRUE```", True),
        ('"True."', True),
        ("<think>?</think>FALSE", False),
    ],
)
async def test_parse_coherence_reply(reply, expected):
    assert semantic_filter.parse_coherence_reply(reply) is expected


@pytest.mark.parametrize("reply", ["", "TRUE porque es un mueble", "Sí", None])
async def test_parse_coherence_reply_rejects_other_answers(reply):
    with pytest.raises(OmniRouteError):
        semantic_filter.parse_coherence_reply(reply)


async def test_validate_furniture_coherence_sends_strict_prompt(monkeypatch):
    sent = {}

    async def fake_chat_completion(messages, temperature):
        sent["messages"], sent["temperature"] = messages, temperature
        return "TRUE" if "Silla" in messages[1]["content"] else "FALSE"

    monkeypatch.setattr(semantic_filter, "chat_completion", fake_chat_completion)

    assert await semantic_filter.validate_furniture_coherence(" Silla ") is True
    system, user = sent["messages"]
    assert "TRUE o FALSE" in system["content"]
    assert user == {"role": "user", "content": '"Silla"'}
    assert sent["temperature"] == 0
    assert await semantic_filter.validate_furniture_coherence("Algoritmo") is False


async def test_delete_catalog_furniture(user_a, db_sessions, fake_validator):
    kept = (await user_a.post("/api/v1/spaces/catalog", json=catalog_item("Silla"))).json()
    removed = (await user_a.post("/api/v1/spaces/catalog", json=catalog_item("Puff"))).json()

    response = await user_a.delete(f"/api/v1/spaces/furniture/{removed['id']}")

    assert response.status_code == 200, response.text
    assert response.json() == {"success": True, "message": "Furniture item successfully removed"}
    async with db_sessions() as session:
        assert [m.id for m in (await session.scalars(select(MuebleCatalogo))).all()] == [kept["id"]]
        # The category is shared with the remaining item, so it is not deleted.
        assert [c.nombre for c in (await session.scalars(select(Categoria))).all()] == ["Sillas"]


async def test_delete_catalog_furniture_not_found(user_a, db_sessions):
    response = await user_a.delete("/api/v1/spaces/furniture/999")

    assert response.status_code == 404
    assert "999" in response.json()["detail"]


async def test_spaces_are_isolated_per_account(user_a, user_b, db_sessions, fake_validator):
    app.dependency_overrides[get_layout_suggester] = lambda: fake_suggester
    try:
        for http, nombre in ((user_a, "Sala A"), (user_b, "Sala B")):
            response = await http.post(
                "/api/v1/spaces/analyze", json={"nombre": nombre, "puntos": room_scan().tolist()}
            )
            assert response.status_code == 201, response.text
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)
    # user_id is still accepted when it matches the session, for older frontends.
    await user_a.post("/api/v1/spaces/catalog", json={**catalog_item("Silla"), "user_id": "user-a"})
    await user_b.post("/api/v1/spaces/catalog", json=catalog_item("Puff"))

    spaces_a = (await user_a.get("/api/v1/spaces")).json()
    spaces_b = (await user_b.get("/api/v1/spaces", params={"user_id": "user-b"})).json()

    assert spaces_a["user_id"] == "user-a"
    assert [r["nombre"] for r in spaces_a["habitaciones"]] == ["Sala A"]
    assert [m["nombre"] for m in spaces_a["habitaciones"][0]["muebles"]] == ["mesa"]
    assert [m["nombre"] for m in spaces_a["catalogo"]] == ["Silla"]
    assert [r["nombre"] for r in spaces_b["habitaciones"]] == ["Sala B"]
    assert [m["nombre"] for m in spaces_b["catalogo"]] == ["Puff"]


async def test_spaces_user_id_is_optional(user_a, db_sessions):
    response = await user_a.get("/api/v1/spaces")

    assert response.status_code == 200, response.text
    assert response.json() == {"user_id": "user-a", "habitaciones": [], "catalogo": []}


async def test_trailing_slash_is_served_without_redirect(user_a, db_sessions):
    response = await user_a.get("/api/v1/spaces/", params={"user_id": "user-a"})

    assert response.status_code == 200, response.text
    assert response.json() == {"user_id": "user-a", "habitaciones": [], "catalogo": []}


async def test_cors_preflight_allows_delete_from_allowed_origin(client):
    origin = "https://proyecto-3d.vercel.app"
    response = await client.options(
        "/api/v1/spaces/furniture/1",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    # Bearer tokens, not cookies: credentials are not allowed.
    assert "access-control-allow-credentials" not in response.headers
    assert "DELETE" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers and "content-type" in allowed_headers


async def test_cors_preflight_rejects_other_origins(client):
    response = await client.options(
        "/api/v1/spaces",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
