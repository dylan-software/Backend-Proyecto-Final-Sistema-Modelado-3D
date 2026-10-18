import time

import pytest
from sqlalchemy import select

from app.geometry.layout import ItemPosition, LayoutResult, run_layout
from app.models.space import Categoria, Habitacion, MuebleCatalogo, MuebleSugerido
from app.routers import rooms
from app.tests.test_room import NOTCHED_ROOM
from app.tests.test_rooms_api import ROOMS, created_id

pytestmark = pytest.mark.anyio

CONSTRAINTS = {"minDistanceBetweenItemsCm": 15, "minClearPathCm": 60}


async def add_catalog(db_sessions, user_id, *items):
    """Catalog rows (name, width_cm, height_cm, depth_cm) for user_id; returns their ids."""
    async with db_sessions() as session:
        category = await session.scalar(select(Categoria).where(Categoria.nombre == "Muebles"))
        if category is None:
            category = Categoria(nombre="Muebles")
            session.add(category)
            await session.flush()
        rows = [
            MuebleCatalogo(
                user_id=user_id, categoria_id=category.id, nombre=n, ancho=w, alto=h, profundidad=d
            )
            for n, w, h, d in items
        ]
        session.add_all(rows)
        await session.commit()
        return [row.id for row in rows]


async def stored_pieces(db_sessions, room_id):
    async with db_sessions() as session:
        query = (
            select(MuebleSugerido).where(MuebleSugerido.habitacion_id == room_id).order_by(MuebleSugerido.id)
        )
        return (await session.scalars(query)).all()


async def layout(client, room_id, **body):
    return await client.post(f"{ROOMS}/{room_id}/layout", json={"constraints": CONSTRAINTS, **body})


async def test_layout_places_the_catalog_and_answers_the_pipeline_result(user_a, db_sessions):
    room_id = await created_id(user_a)
    ids = await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60), ("Silla", 45, 90, 50))

    response = await layout(user_a, room_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"layout", "validation", "lighting", "readyToRender", "errors"}
    assert [p["itemId"] for p in body["layout"]["positions"]] == ids
    first = body["layout"]["positions"][0]
    assert set(first) == {"itemId", "x", "y", "rotation"}
    assert (first["x"], first["y"]) == pytest.approx((0.15, 0.15))
    assert body["layout"]["warnings"] == []
    assert body["validation"] == {"valid": True, "collisions": []}
    assert body["lighting"]["source"] == "neutral_preset"
    assert body["readyToRender"] is True
    assert body["errors"] == []


async def test_layout_is_persisted_as_footprint_centers_in_meters(user_a, db_sessions):
    """Units (F5): the catalog is in cm, rooms and placed furniture in m, converted in one place."""
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60))

    await layout(user_a, room_id)

    (piece,) = await stored_pieces(db_sessions, room_id)
    assert piece.nombre == "Mesa"
    # 100 x 75 x 60 cm in the catalog -> 1 x 0.75 x 0.6 m placed.
    assert (piece.ancho, piece.alto, piece.profundidad) == pytest.approx((1.0, 0.75, 0.6))
    # API corner (0.15, 0.15) -> stored center (0.15 + 0.5, 0.15 + 0.3); y = 0 is the floor.
    assert (piece.posicion_x, piece.posicion_y, piece.posicion_z) == pytest.approx((0.65, 0.0, 0.45))


async def test_items_return_the_minimum_corner(user_a, db_sessions):
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60))
    await layout(user_a, room_id)

    response = await user_a.get(f"{ROOMS}/{room_id}/items")

    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert set(item) == {"id", "name", "x", "y", "rotation"}
    assert item["name"] == "Mesa"
    assert (item["x"], item["y"], item["rotation"]) == pytest.approx((0.15, 0.15, 0))


async def test_items_of_a_room_without_furniture_is_empty(user_a, db_sessions):
    room_id = await created_id(user_a)

    response = await user_a.get(f"{ROOMS}/{room_id}/items")

    assert response.json() == {"items": []}


async def test_a_new_layout_replaces_the_previous_furniture(user_a, db_sessions):
    room_id = await created_id(user_a)
    mesa, silla = await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60), ("Silla", 45, 90, 50))
    await layout(user_a, room_id)

    response = await layout(user_a, room_id, itemIds=[silla])

    assert response.status_code == 200
    assert [p.nombre for p in await stored_pieces(db_sessions, room_id)] == ["Silla"]


async def test_item_ids_choose_and_order_the_pieces(user_a, db_sessions):
    room_id = await created_id(user_a)
    mesa, silla, lampara = await add_catalog(
        db_sessions, "user-a", ("Mesa", 100, 75, 60), ("Silla", 45, 90, 50), ("Lámpara", 30, 150, 30)
    )

    response = await layout(user_a, room_id, itemIds=[lampara, mesa, lampara])

    assert [p["itemId"] for p in response.json()["layout"]["positions"]] == [lampara, mesa]


async def test_layout_never_uses_the_notch_of_the_real_room(user_a, db_sessions):
    room_id = await created_id(user_a, vertices=NOTCHED_ROOM, height_m=2.5)
    sizes = [(400, 50), (120, 60), (90, 90), (200, 40), (60, 60)] * 3
    await add_catalog(db_sessions, "user-a", *[(f"Mueble {i}", w, 80, d) for i, (w, d) in enumerate(sizes)])

    response = await layout(
        user_a, room_id, constraints={"minDistanceBetweenItemsCm": 10, "minClearPathCm": 60}
    )

    assert response.status_code == 200
    assert response.json()["layout"]["positions"]
    for piece in await stored_pieces(db_sessions, room_id):
        min_x, max_x = piece.posicion_x - piece.ancho / 2, piece.posicion_x + piece.ancho / 2
        min_z, max_z = piece.posicion_z - piece.profundidad / 2, piece.posicion_z + piece.profundidad / 2
        assert not (min_x < 4 and max_x > 2 and min_z < 1.5 and max_z > 0), piece.nombre


async def test_pieces_that_do_not_fit_come_back_as_warnings(user_a, db_sessions):
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60), ("Escenario", 900, 100, 50))

    body = (await layout(user_a, room_id)).json()

    assert len(body["layout"]["positions"]) == 1
    assert "Escenario" in body["layout"]["warnings"][0]
    assert body["readyToRender"] is True


async def test_a_layout_with_collisions_is_not_stored(user_a, db_sessions, monkeypatch):
    room_id = await created_id(user_a)
    mesa, silla = await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60), ("Silla", 100, 90, 60))

    def colliding_layout(room, pieces, min_distance_cm):
        def generator(room, pieces, min_distance_cm):
            return LayoutResult(positions=[ItemPosition(mesa, 0.5, 0.5), ItemPosition(silla, 0.7, 0.5)])

        return run_layout(room, pieces, min_distance_cm, generator=generator)

    monkeypatch.setattr(rooms, "run_layout", colliding_layout)
    body = (await layout(user_a, room_id)).json()

    assert body["readyToRender"] is False
    assert body["validation"]["collisions"] == [{"itemA": mesa, "itemB": silla, "overlapCm": 60.0}]
    assert await stored_pieces(db_sessions, room_id) == []


async def test_the_layout_runs_in_a_worker_thread(user_a, db_sessions, monkeypatch):
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60))
    real_to_thread = rooms.asyncio.to_thread
    calls = []

    async def spy(func, *args):
        calls.append(func)
        return await real_to_thread(func, *args)

    monkeypatch.setattr(rooms.asyncio, "to_thread", spy)
    response = await layout(user_a, room_id)

    assert response.status_code == 200
    assert response.json()["readyToRender"] is True
    assert calls == [rooms.run_layout]


async def test_empty_catalog_is_422(user_a, db_sessions):
    room_id = await created_id(user_a)

    response = await layout(user_a, room_id)

    assert response.status_code == 422


async def test_a_catalog_too_large_to_lay_out_without_item_ids_is_422_quickly(user_a, db_sessions):
    room_id = await created_id(user_a)
    ids = await add_catalog(db_sessions, "user-a", *[(f"Silla {i}", 45, 90, 50) for i in range(5000)])

    started = time.perf_counter()
    response = await layout(user_a, room_id)
    elapsed = time.perf_counter() - started

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Tu catálogo tiene demasiados muebles para distribuirlos todos; envía itemIds, máximo 200."
    )
    assert elapsed < 2  # LIMIT 201: the 5000 rows are never loaded
    assert await stored_pieces(db_sessions, room_id) == []
    # The same catalog can still be laid out by choosing up to 200 pieces.
    assert (await layout(user_a, room_id, itemIds=ids[:200])).status_code == 200


async def test_exactly_200_pieces_without_item_ids_are_laid_out(user_a, db_sessions):
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", *[(f"Cojín {i}", 10, 10, 10) for i in range(200)])

    assert (await layout(user_a, room_id)).status_code == 200


@pytest.mark.parametrize(
    "constraints",
    [
        {"minDistanceBetweenItemsCm": 0, "minClearPathCm": 60},
        {"minDistanceBetweenItemsCm": 15},
        {},
    ],
)
async def test_invalid_constraints_are_422(user_a, db_sessions, constraints):
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60))

    assert (await layout(user_a, room_id, constraints=constraints)).status_code == 422


# --- Authentication and ownership ------------------------------------------------------------------


async def test_layout_and_items_need_a_token(client, db_sessions):
    assert (await client.get(f"{ROOMS}/1/items")).status_code == 401
    assert (await client.post(f"{ROOMS}/1/layout", json={"constraints": CONSTRAINTS})).status_code == 401


async def test_another_account_cannot_lay_out_or_list_a_room(user_a, user_b, db_sessions):
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60))
    await add_catalog(db_sessions, "user-b", ("Cama", 140, 50, 200))
    await layout(user_a, room_id)

    assert (await user_b.get(f"{ROOMS}/{room_id}/items")).status_code == 404
    assert (await layout(user_b, room_id)).status_code == 404
    assert [p.nombre for p in await stored_pieces(db_sessions, room_id)] == ["Mesa"]


async def test_catalog_items_of_another_account_are_404(user_a, db_sessions):
    room_id = await created_id(user_a)
    (mesa,) = await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60))
    (cama,) = await add_catalog(db_sessions, "user-b", ("Cama", 140, 50, 200))

    response = await layout(user_a, room_id, itemIds=[mesa, cama])

    assert response.status_code == 404
    assert str(cama) in response.json()["detail"]


async def test_layout_reads_the_room_from_the_database_not_the_body(user_a, db_sessions):
    room_id = await created_id(user_a)
    await add_catalog(db_sessions, "user-a", ("Mesa", 100, 75, 60))

    # Geometry sent by the client is ignored: only constraints and itemIds are read.
    response = await layout(user_a, room_id, room={"vertices": [{"x": 0, "y": 0}], "heightM": 99})

    assert response.status_code == 200
    async with db_sessions() as session:
        assert (await session.get(Habitacion, room_id)).alto == 2.6
