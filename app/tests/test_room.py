import math

import pytest
from sqlalchemy import select

from app.database.connection import add_missing_room_columns, add_missing_user_columns
from app.geometry.room import Point, Room, RoomError
from app.geometry.units import cm_to_m, m_to_cm
from app.models.space import Habitacion

# L-shaped room: a 4 x 5 rectangle with its 2 x 2 top-right corner cut away (6 vertices).
L_SHAPE = [(0, 0), (4, 0), (4, 3), (2, 3), (2, 5), (0, 5)]


def irregular_room():
    return Room(L_SHAPE, 2.6)


def as_tuples(room):
    return [(p.x, p.y) for p in room.vertices]


# --- Ported from tests/domain.test.ts ("Room — creation") ---------------------------------------


def test_creates_a_valid_irregular_polygon():
    room = irregular_room()

    assert len(room.vertices) == 6
    assert room.height_m == 2.6


def test_rejects_fewer_than_3_vertices():
    with pytest.raises(RoomError, match="at least 3"):
        Room([(0, 0), (1, 0)], 2.6)


def test_rejects_collinear_points():
    with pytest.raises(RoomError, match="area"):
        Room([(0, 0), (1, 0), (2, 0)], 2.6)


@pytest.mark.parametrize("height", [0, -1, math.nan, math.inf])
def test_rejects_invalid_height(height):
    with pytest.raises(RoomError, match="height_m"):
        Room([(0, 0), (4, 0), (4, 4), (0, 4)], height)


def test_area_of_the_l_shaped_room():
    # 4 x 5 = 20 minus the 2 x 2 cut corner = 16.
    assert irregular_room().area_m2() == pytest.approx(16)


def test_area_does_not_depend_on_vertex_order():
    assert Room(list(reversed(L_SHAPE)), 2.6).area_m2() == pytest.approx(16)


# --- Ported: point-in-polygon with an irregular shape -------------------------------------------


def test_point_in_the_main_body_is_inside():
    assert irregular_room().contains_point(1, 1) is True


def test_point_in_the_cut_corner_is_outside():
    assert irregular_room().contains_point(3, 4) is False


def test_contains_rectangle_respects_the_real_shape():
    room = irregular_room()

    assert room.contains_rectangle(0.5, 0.5, 1, 1) is True
    # Fits in the 4 x 5 bounding box but reaches into the cut corner.
    assert room.contains_rectangle(2.5, 2.5, 1, 1) is False


# Real 21-vertex room (meters) with a notch at the top: x in [2, 4], y in [0, 1.5] is outside.
NOTCHED_ROOM = [
    (0, 0),
    (2, 0),
    (2, 1.5),
    (4, 1.5),
    (4, 0),
    (6, 0),
    (6, 1.5),
    (7.5, 1.5),
    (7.5, 4),
    (6, 4),
    (6, 5),
    (4.5, 5),
    (4.5, 3),
    (2.5, 3),
    (2.5, 4),
    (3.5, 4),
    (3.5, 5),
    (3.5, 6),
    (2.5, 6),
    (2.5, 5),
    (0, 5),
]


def test_rectangle_with_every_corner_inside_can_still_cross_a_notch():
    room = Room(NOTCHED_ROOM, 2.5)

    # Corners at x = 1 and x = 5 are both in the floor, but the middle crosses the notch.
    assert room.contains_rectangle(1, 0.2, 4, 0.5) is False
    assert room.contains_rectangle(0.2, 0.2, 1.5, 0.5) is True
    assert room.contains_rectangle(4.2, 0.2, 1.5, 0.5) is True
    # Exactly the notch: every corner is on a wall and no wall crosses it, but it is outside the room.
    assert room.contains_rectangle(2, 0, 2, 1.5) is False


def test_rectangle_pushed_against_walls_is_inside():
    room = irregular_room()

    # Shares the x = 0 and y = 0 walls, and the inner corner's walls of the L.
    assert room.contains_rectangle(0, 0, 4, 3) is True
    assert room.contains_rectangle(0, 3, 2, 2) is True


def test_rectangle_spanning_the_inner_corner_of_the_l_is_outside():
    # Every corner is inside the L, but the reflex vertex (2, 3) sits inside the rectangle.
    room = Room([(0, 0), (4, 0), (4, 3), (2, 3), (2, 5), (0, 5)], 2.6)

    assert room.contains_rectangle(1, 2, 0.9, 2) is True
    assert room.contains_rectangle(1.5, 2.5, 1, 1) is False


def test_bounding_box():
    box = irregular_room().bounding_box()

    assert (box.min_x, box.min_y, box.max_x, box.max_y) == (0, 0, 4, 5)


# --- Ported: editing the surface after creation -------------------------------------------------


def test_move_vertex_only_changes_that_vertex():
    room = irregular_room()
    room.move_vertex(0, {"x": -1, "y": -1})

    assert room.vertices[0] == Point(-1, -1)
    assert room.vertices[1] == Point(4, 0)


def test_move_vertex_onto_its_neighbour_is_allowed():
    # Node test: collapsing an edge to zero length is degenerate but still valid.
    room = irregular_room()
    room.move_vertex(0, (4, 0))

    assert room.vertices[0] == room.vertices[1] == Point(4, 0)
    # Shoelace of (4,0) (4,0) (4,3) (2,3) (2,5) (0,5): 0 + 12 + 6 + 4 + 10 - 20 = 12, halved.
    assert room.area_m2() == pytest.approx(6)


def test_set_vertices_rejects_a_degenerate_shape():
    room = irregular_room()

    with pytest.raises(RoomError):
        room.set_vertices([(0, 0), (1, 0), (2, 0)])
    assert as_tuples(room) == L_SHAPE  # a rejected edit leaves the room unchanged


def test_move_vertex_out_of_range():
    with pytest.raises(RoomError, match="out of range"):
        irregular_room().move_vertex(99, (0, 0))


def test_insert_vertex_splits_an_edge():
    room = irregular_room()
    room.insert_vertex(0, (2, -1))

    assert len(room.vertices) == 7
    assert room.vertices[1] == Point(2, -1)


@pytest.mark.parametrize("after_index", [-1, 6])
def test_insert_vertex_out_of_range(after_index):
    with pytest.raises(RoomError, match="out of range"):
        irregular_room().insert_vertex(after_index, (2, -1))


def test_remove_vertex():
    room = irregular_room()
    room.remove_vertex(5)

    assert len(room.vertices) == 5


def test_remove_vertex_never_goes_below_a_triangle():
    room = Room([(0, 0), (4, 0), (0, 4)], 2.6)

    with pytest.raises(RoomError, match="at least 3"):
        room.remove_vertex(0)


def test_remove_vertex_out_of_range():
    with pytest.raises(RoomError, match="out of range"):
        irregular_room().remove_vertex(6)


def test_set_vertices_replaces_the_whole_shape():
    room = irregular_room()
    room.set_vertices([(0, 0), (3, 0), (3, 3), (0, 3)])

    assert as_tuples(room) == [(0, 0), (3, 0), (3, 3), (0, 3)]
    assert room.area_m2() == pytest.approx(9)


def test_record_round_trip_after_an_edit():
    # Replaces the Node GeoJSON round trip: the JSON column is what the backend persists.
    room = irregular_room()
    room.move_vertex(2, (5, 2))
    record = room.to_record()

    restored = Room.from_record(record["vertices"], record["ancho"], record["profundidad"], record["alto"])

    assert restored.vertices == room.vertices
    assert restored.height_m == room.height_m


# --- New: self-intersection --------------------------------------------------------------------


def test_rejects_crossing_edges():
    # Square with vertex 2 dragged across the left wall: edge 1-2 crosses edge 3-0.
    room = Room([(0, 0), (4, 0), (4, 4), (0, 4)], 2.6)

    with pytest.raises(RoomError, match="intersects itself"):
        room.move_vertex(2, (-1, 3))
    assert as_tuples(room) == [(0, 0), (4, 0), (4, 4), (0, 4)]


def test_rejects_a_vertex_touching_a_non_adjacent_edge():
    # Inner corner (2, 3) of the L moved onto the left wall x = 0, which is not one of its edges.
    room = Room([(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)], 2.6)

    with pytest.raises(RoomError, match="intersects itself"):
        room.move_vertex(3, (0, 2))


def test_rejects_an_edge_folding_back_over_its_neighbour():
    with pytest.raises(RoomError, match="intersects itself"):
        Room([(0, 0), (4, 0), (2, 0), (2, 3)], 2.6)


def test_ignores_zero_length_edges_from_repeated_vertices():
    room = Room([(0, 0), (4, 0), (4, 0), (4, 4), (0, 4), (0, 0)], 2.6)

    assert room.area_m2() == pytest.approx(16)


def test_repeated_vertices_do_not_hide_a_real_crossing():
    with pytest.raises(RoomError, match="intersects itself"):
        Room([(0, 0), (4, 0), (4, 0), (-1, 3), (0, 4)], 2.6)


def test_collinear_redundant_vertex_is_allowed():
    room = Room([(0, 0), (2, 0), (4, 0), (4, 4), (0, 4)], 2.6)

    assert room.area_m2() == pytest.approx(16)


def test_concave_room_that_is_simple_is_allowed():
    # U shape: deep notch, no edges touching.
    room = Room([(0, 0), (6, 0), (6, 4), (4, 4), (4, 1), (2, 1), (2, 4), (0, 4)], 2.6)

    assert room.area_m2() == pytest.approx(24 - 6)


@pytest.mark.parametrize("bad", [(math.nan, 0), (0, math.inf)])
def test_rejects_non_finite_coordinates(bad):
    with pytest.raises(RoomError, match="finite"):
        Room([(0, 0), (4, 0), bad], 2.6)


# --- Rooms stored in habitaciones --------------------------------------------------------------


def test_room_without_vertices_is_the_analyze_rectangle():
    room = Room.from_record(None, ancho=4, profundidad=5, alto=3)

    assert as_tuples(room) == [(0, 0), (4, 0), (4, 5), (0, 5)]
    assert room.height_m == 3
    assert room.area_m2() == pytest.approx(20)


def test_record_keeps_ancho_profundidad_as_bounding_box():
    record = Room([(1, 2), (5, 2), (5, 4), (3, 7)], 2.5).to_record()

    assert record["ancho"] == 4
    assert record["profundidad"] == 5
    assert record["alto"] == 2.5
    assert record["vertices"] == [{"x": 1, "y": 2}, {"x": 5, "y": 2}, {"x": 5, "y": 4}, {"x": 3, "y": 7}]


@pytest.mark.anyio
async def test_vertices_column_round_trips_through_the_database(db_sessions):
    record = irregular_room().to_record()
    async with db_sessions() as session:
        session.add(Habitacion(user_id="user-a", nombre="Sala en L", **record))
        session.add(Habitacion(user_id="user-a", nombre="Escaneada", ancho=4, alto=3, profundidad=5))
        await session.commit()

    async with db_sessions() as session:
        rooms = (await session.scalars(select(Habitacion).order_by(Habitacion.id))).all()

    assert rooms[0].vertices == record["vertices"]
    assert rooms[1].vertices is None
    restored = Room.from_record(rooms[0].vertices, rooms[0].ancho, rooms[0].profundidad, rooms[0].alto)
    assert restored.area_m2() == pytest.approx(16)


# --- Migration ---------------------------------------------------------------------------------


class RecordingConnection:
    def __init__(self, dialect):
        self.dialect = type("Dialect", (), {"name": dialect})()
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))


@pytest.mark.anyio
async def test_migration_adds_vertices_column_on_postgresql():
    conn = RecordingConnection("postgresql")

    await add_missing_room_columns(conn)

    assert conn.statements == ["ALTER TABLE habitaciones ADD COLUMN IF NOT EXISTS vertices JSONB"]


@pytest.mark.anyio
async def test_migrations_do_nothing_on_sqlite():
    conn = RecordingConnection("sqlite")

    await add_missing_room_columns(conn)
    await add_missing_user_columns(conn)

    assert conn.statements == []


@pytest.mark.anyio
async def test_startup_runs_the_vertices_migration(monkeypatch):
    from app import main

    calls = []

    async def fake_migration(conn):
        calls.append(conn.dialect.name)

    monkeypatch.setattr(main, "add_missing_room_columns", fake_migration)
    async with main.lifespan(main.app):
        pass

    # conftest.py points DATABASE_URL at an in-memory SQLite, so this never touches a real database.
    assert calls == ["sqlite"]


# --- Units (F5) --------------------------------------------------------------------------------


def test_catalog_centimeters_convert_to_room_meters():
    assert cm_to_m(250) == 2.5
    assert m_to_cm(0.45) == pytest.approx(45)


@pytest.mark.anyio
async def test_catalog_stores_sizes_in_centimeters_as_sent(user_a, db_sessions):
    from app.main import app
    from app.models.space import MuebleCatalogo
    from app.routers.spaces import get_coherence_validator

    async def accept(nombre):
        return True

    app.dependency_overrides[get_coherence_validator] = lambda: accept
    try:
        response = await user_a.post(
            "/api/v1/spaces/catalog",
            json={"nombre": "Sofá", "categoria": "sofa", "ancho": 200, "alto": 80, "profundidad": 90},
        )
    finally:
        app.dependency_overrides.pop(get_coherence_validator, None)

    assert response.status_code == 201, response.text
    async with db_sessions() as session:
        item = await session.scalar(select(MuebleCatalogo))
    # Stored in cm, exactly as the form sent them; a 2 m wide sofa is 200, not 2.
    assert (item.ancho, item.alto, item.profundidad) == (200, 80, 90)
    assert cm_to_m(item.ancho) == 2.0
