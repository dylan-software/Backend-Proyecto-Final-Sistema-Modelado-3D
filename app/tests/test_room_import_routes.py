"""POST /api/v1/spaces/rooms/import (CONTRATO-FASE6.md): dueño, ids nuevos, ida y vuelta, 401, 413, 415,
422 sin eco, 429 y fallos de la base."""

import copy
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.main import app
from app.models.space import Habitacion, MuebleSugerido
from app.schemas.room import MAX_VERTICES
from app.services.project_import import MAX_IMPORT_BYTES, NOT_JSON, WRONG_VERSION
from app.services.room_undo import RoomUndoStore, get_room_undo_store
from app.tests.test_project_import import MARKER, VALID
from app.tests.test_rooms_api import ROOMS, circle, created_id
from app.utils import rate_limit

pytestmark = pytest.mark.anyio

IMPORT = f"{ROOMS}/import"
JSON_TYPE = {"Content-Type": "application/json"}
RECTANGLE = [(0, 0), (4, 0), (4, 3), (0, 3)]
TABLE = {"name": "Mesa", "kind": "floor", "widthCm": 120, "depthCm": 80, "heightCm": 75}
PICTURE = {"name": "Cuadro", "kind": "wall", "wall": "left", "widthCm": 60, "depthCm": 5, "heightCm": 40}
SHELF = {"name": "Repisa", "kind": "wall", "wall": "back", "widthCm": 80, "depthCm": 20, "heightCm": 5}


@pytest.fixture
def undo_store():
    fresh = RoomUndoStore()
    app.dependency_overrides[get_room_undo_store] = lambda: fresh
    yield fresh
    app.dependency_overrides.pop(get_room_undo_store, None)


async def send(client, data, headers=JSON_TYPE):
    body = data if isinstance(data, bytes) else json.dumps(data).encode()
    return await client.post(IMPORT, content=body, headers=headers)


async def counts(db_sessions):
    async with db_sessions() as session:
        rooms = await session.scalar(select(func.count()).select_from(Habitacion))
        pieces = await session.scalar(select(func.count()).select_from(MuebleSugerido))
    return rooms, pieces


def export_file(room, items):
    """El mismo mapeo que JsonProjectExporter del frontend (src/lib/projectExport.ts)."""
    return {
        "format": "habita-project",
        "version": 1,
        "name": room["name"],
        "room": {
            "height_m": room["heightM"],
            "vertices": [[vertex["x"], vertex["y"]] for vertex in room["vertices"]],
            "estimated": False,
        },
        "items": [
            {
                "name": item["name"],
                "category": None,
                "kind": "wall" if item["elevationM"] > 0 else "floor",
                "size_cm": [round(item[key] * 100, 4) for key in ("widthM", "heightM", "depthM")],
                "position": {
                    "x_m": round(item["x"] + item["widthM"] / 2, 6),
                    "z_m": round(item["y"] + item["depthM"] / 2, 6),
                    "elevation_m": item["elevationM"],
                    "rotation_deg": 0,
                },
                "wall": None,
                "parts": [],
            }
            for item in items
        ],
    }


# --- Éxito --------------------------------------------------------------------------------------------


async def test_import_creates_a_new_project_of_the_account(user_a, db_sessions):
    response = await send(user_a, VALID)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"room", "items"}
    room = body["room"]
    assert (room["name"], room["heightM"], room["areaM2"]) == ("Prueba 1", 2.5, 12)
    assert "groupId" not in room and "description" not in room
    assert [item["name"] for item in body["items"]] == ["Cama", "Cuadro"]
    bed = body["items"][0]
    assert (bed["x"], bed["y"], bed["widthM"], bed["depthM"], bed["elevationM"]) == pytest.approx(
        (0.5, 0.55, 1.4, 1.9, 0)
    )
    async with db_sessions() as session:
        stored = await session.get(Habitacion, room["id"])
    assert (stored.user_id, stored.group_id, stored.description) == ("user-a", None, None)
    listed = (await user_a.get(f"{ROOMS}/{room['id']}/items")).json()["items"]
    assert listed == body["items"]


async def test_the_project_belongs_only_to_the_importer(user_a, user_b, db_sessions):
    room_id = (await send(user_a, VALID)).json()["room"]["id"]

    assert (await user_b.get(f"{ROOMS}/{room_id}")).status_code == 404
    assert (await user_a.get(f"{ROOMS}/{room_id}")).status_code == 200


async def test_every_import_gets_new_ids(user_a, db_sessions):
    first = (await send(user_a, VALID)).json()
    second = (await send(user_a, VALID)).json()

    assert first["room"]["id"] != second["room"]["id"]
    first_ids = {item["id"] for item in first["items"]}
    second_ids = {item["id"] for item in second["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert await counts(db_sessions) == (2, 4)


async def test_round_trip_keeps_sizes_positions_and_heights_with_new_ids(user_a, db_sessions, undo_store):
    # Sala con un objeto de suelo y dos de pared, exportada como lo hace el frontend e importada.
    room_id = await created_id(user_a, vertices=RECTANGLE, height_m=2.7, name="Dormitorio")
    for body in (TABLE, PICTURE, SHELF):
        assert (await user_a.post(f"{ROOMS}/{room_id}/items", json=body)).status_code == 201
    room = (await user_a.get(f"{ROOMS}/{room_id}")).json()
    items = (await user_a.get(f"{ROOMS}/{room_id}/items")).json()["items"]
    assert sorted(item["elevationM"] > 0 for item in items) == [False, True, True]

    response = await send(user_a, export_file(room, items))

    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["room"]["id"] != room_id
    assert imported["room"]["name"] == "Dormitorio"
    assert imported["room"]["vertices"] == room["vertices"]
    assert imported["room"]["heightM"] == room["heightM"]
    assert imported["room"]["areaM2"] == room["areaM2"]
    assert len(imported["items"]) == len(items)
    keys = ("name", "x", "y", "rotation", "widthM", "depthM", "heightM", "elevationM")
    for original, copied in zip(items, imported["items"], strict=True):
        assert copied["id"] != original["id"]
        for key in keys:
            assert copied[key] == pytest.approx(original[key], abs=1e-6), key
    assert not {item["id"] for item in items} & {item["id"] for item in imported["items"]}


async def test_round_trip_of_a_room_with_more_than_60_vertices(user_a, db_sessions, undo_store):
    # Antes el archivo tenía un tope de 60 vértices: una sala así se exportaba pero no se reimportaba.
    # Ahora el tope es el de POST /rooms (y el de insertar vértices), así que la sala más grande vuelve.
    room_id = await created_id(user_a, vertices=circle(MAX_VERTICES, radius=3), height_m=2.5, name="Redonda")
    assert (await user_a.post(f"{ROOMS}/{room_id}/items", json=TABLE)).status_code == 201
    room = (await user_a.get(f"{ROOMS}/{room_id}")).json()
    items = (await user_a.get(f"{ROOMS}/{room_id}/items")).json()["items"]
    assert len(room["vertices"]) == MAX_VERTICES > 60

    response = await send(user_a, export_file(room, items))

    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["room"]["id"] != room_id
    assert imported["room"]["vertices"] == room["vertices"]
    assert imported["room"]["areaM2"] == room["areaM2"]
    keys = ("x", "y", "widthM", "depthM", "heightM", "elevationM")
    (copied,) = imported["items"]
    for key in keys:
        assert copied[key] == pytest.approx(items[0][key], abs=1e-6), key


async def test_import_does_not_spend_ai_quota_and_has_nothing_to_undo(user_a, db_sessions, undo_store):
    room_id = (await send(user_a, VALID)).json()["room"]["id"]

    assert rate_limit.ai_limiter().retry_after("user-a", get_settings().ai_requests_per_minute) == 0
    assert (await user_a.post(f"{ROOMS}/{room_id}/undo")).status_code == 409


async def test_content_type_with_charset_is_fine(user_a, db_sessions):
    response = await send(user_a, VALID, headers={"Content-Type": "application/json; charset=utf-8"})

    assert response.status_code == 201


# --- Sesión, tipo y tamaño ----------------------------------------------------------------------------


async def test_import_needs_a_token(client, db_sessions):
    assert (await send(client, VALID)).status_code == 401
    assert await counts(db_sessions) == (0, 0)


@pytest.mark.parametrize(
    "content_type", ["text/plain", "multipart/form-data; boundary=b", "application/xml", ""]
)
async def test_other_content_types_are_415(user_a, db_sessions, content_type):
    response = await send(user_a, VALID, headers={"Content-Type": content_type})

    assert response.status_code == 415
    assert response.json()["detail"] == "El archivo debe enviarse como JSON."


async def test_a_body_over_256_kb_is_413_before_authenticating(client, db_sessions):
    body = b" " * (MAX_IMPORT_BYTES + 1)

    response = await send(client, body)

    assert response.status_code == 413


async def test_a_huge_declared_length_is_413_without_reading(client, db_sessions):
    headers = {**JSON_TYPE, "Content-Length": str(50 * 1024 * 1024)}

    response = await client.post(IMPORT, content=b"{}", headers=headers)

    assert response.status_code == 413


async def test_a_chunked_body_over_256_kb_is_413(client, db_sessions):
    async def chunks():
        for _ in range(5):
            yield b" " * (64 * 1024)

    response = await client.post(IMPORT, content=chunks(), headers=JSON_TYPE)

    assert response.status_code == 413


async def test_exactly_256_kb_is_read(user_a, db_sessions):
    text = json.dumps(VALID).encode()
    body = text + b" " * (MAX_IMPORT_BYTES - len(text))  # espacios al final: JSON válido

    response = await send(user_a, body)

    assert len(body) == MAX_IMPORT_BYTES
    assert response.status_code == 201, response.text


async def test_the_slash_variant_has_the_same_limit(client, db_sessions):
    response = await client.post(f"{IMPORT}/", content=b" " * (MAX_IMPORT_BYTES + 1), headers=JSON_TYPE)

    assert response.status_code == 413


# --- 422 sin eco --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [b"\xff\xfe\x00{", b"{", b"", b'{"format": "habita-project", "version": NaN}'],
    ids=["not_utf8", "open", "empty", "nan"],
)
async def test_malformed_bodies_are_422_never_500(user_a, db_sessions, body):
    response = await send(user_a, body)

    assert response.status_code == 422
    assert response.json() == {"detail": NOT_JSON}


async def test_another_version_is_422(user_a, db_sessions):
    response = await send(user_a, {**VALID, "version": 2})

    assert response.json() == {"detail": WRONG_VERSION}


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(id=7, user_id=MARKER),
        lambda data: data["room"].update({MARKER: True}),
        lambda data: data["items"][0].update(id=MARKER),
        lambda data: data["items"][0].update(name=f"<script>{MARKER}()</script>"),
        lambda data: data["items"][1]["parts"][0].update(color=f"#{MARKER}"),
        lambda data: data.update(name=MARKER * 50),
    ],
    ids=["root_ids", "room_extra", "item_id", "item_name", "part_color", "long_name"],
)
async def test_invalid_files_are_422_without_echo_and_store_nothing(user_a, db_sessions, change):
    data = copy.deepcopy(VALID)
    change(data)

    response = await send(user_a, data)

    assert response.status_code == 422
    assert MARKER not in response.text
    assert isinstance(response.json()["detail"], str)
    assert await counts(db_sessions) == (0, 0)


# --- 429 ----------------------------------------------------------------------------------------------


async def test_too_many_imports_in_an_hour_are_429(user_a, user_b, db_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "imports_per_hour", 2)

    statuses = [(await send(user_a, VALID)).status_code for _ in range(2)]
    refused = await send(user_a, VALID)

    assert statuses == [201, 201]
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert "importado demasiados proyectos" in refused.json()["detail"]
    assert (await send(user_b, VALID)).status_code == 201  # cada cuenta tiene su cupo
    assert await counts(db_sessions) == (3, 6)


async def test_rejected_files_do_not_count(user_a, db_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "imports_per_hour", 1)

    for _ in range(5):
        assert (await send(user_a, b"{")).status_code == 422

    assert (await send(user_a, VALID)).status_code == 201
    assert (await send(user_a, VALID)).status_code == 429


# --- Base de datos ------------------------------------------------------------------------------------


async def test_a_failed_commit_is_503_and_stores_nothing(user_a, db_sessions, monkeypatch):
    async def failing_commit(self):
        raise OSError("connection lost")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", failing_commit)
        response = await send(user_a, VALID)

    assert response.status_code == 503
    assert "connection lost" not in response.text
    assert await counts(db_sessions) == (0, 0)
    # Un fallo no gasta cupo.
    assert rate_limit.import_limiter().retry_after("user-a", get_settings().imports_per_hour) == 0
