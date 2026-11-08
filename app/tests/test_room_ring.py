"""Room guarda sus paredes en un anillo (CircularDoublyLinkedList): pared siguiente y anterior."""

import random

import pytest

from app.geometry.room import Point, Room, RoomError
from app.structures import CircularDoublyLinkedList

SQUARE = [(0, 0), (4, 0), (4, 3), (0, 3)]


def as_tuples(walls):
    return [((a.x, a.y), (b.x, b.y)) for a, b in walls]


def test_the_vertices_live_in_a_ring():
    room = Room(SQUARE, 2.5)
    assert isinstance(room._ring, CircularDoublyLinkedList)
    room._ring.check_invariants()
    assert room._ring.head.prev.value == Point(0, 3)  # el último enlaza con el primero


def test_walls_close_the_polygon():
    assert as_tuples(Room(SQUARE, 2.5).walls()) == [
        ((0, 0), (4, 0)),
        ((4, 0), (4, 3)),
        ((4, 3), (0, 3)),
        ((0, 3), (0, 0)),
    ]


def test_neighbor_walls_wrap_around_at_both_ends():
    room = Room(SQUARE, 2.5)
    walls = as_tuples(room.walls())
    for index in range(4):
        previous, following = room.neighbor_walls(index)
        assert as_tuples([previous, following]) == [walls[index - 1], walls[(index + 1) % 4]]


@pytest.mark.parametrize("index", [-1, 4, 10])
def test_neighbor_walls_out_of_range(index):
    with pytest.raises(RoomError, match="neighbor_walls"):
        Room(SQUARE, 2.5).neighbor_walls(index)


def test_rejected_edits_leave_the_ring_untouched():
    room = Room(SQUARE, 2.5)
    before = room.vertices
    with pytest.raises(RoomError):
        room.move_vertex(0, (4, 3))  # se cruzaría
    with pytest.raises(RoomError):
        room.insert_vertex(3, (5, 1.5))  # la pared de cierre cruzaría otra
    triangle = Room([(0, 0), (4, 0), (2, 3)], 2.5)
    with pytest.raises(RoomError):
        triangle.remove_vertex(0)
    room.insert_vertex(0, (2, 0))  # colineal: válido, 5 vértices
    assert room.vertices == (Point(0, 0), Point(2, 0)) + before[1:]
    room._ring.check_invariants()


def test_a_rejected_removal_of_the_head_restores_it_as_head():
    # Sin el vértice 0 queda un triángulo casi plano (área < MIN_AREA_M2): se rechaza y la cabeza vuelve.
    shape = [(0, 1), (0, 0), (2, 0), (2, 0.001)]
    room = Room(shape, 2.5)
    with pytest.raises(RoomError, match="area"):
        room.remove_vertex(0)
    assert room.vertices == tuple(Point(x, y) for x, y in shape)
    assert room._ring.head.value == Point(0, 1)
    room._ring.check_invariants()


def test_inserting_after_the_last_vertex_splits_the_closing_wall():
    room = Room(SQUARE, 2.5)
    room.insert_vertex(3, (0, 1.5))
    assert room.vertices[-1] == Point(0, 1.5)
    assert room.vertices[0] == Point(0, 0)
    assert as_tuples(room.walls())[-1] == ((0, 1.5), (0, 0))


def test_random_edits_keep_the_ring_consistent():
    rng = random.Random(7)
    room = Room(SQUARE, 2.5)
    for _ in range(500):
        n = len(room.vertices)
        index = rng.randrange(n)
        point = (rng.uniform(-1, 5), rng.uniform(-1, 4))
        action = rng.choice(("move", "insert", "remove"))
        try:
            if action == "move":
                room.move_vertex(index, point)
            elif action == "insert":
                room.insert_vertex(index, point)
            else:
                room.remove_vertex(index)
        except RoomError:
            pass
        room._ring.check_invariants()
        assert len(room._ring) == len(room.vertices) >= 3
        assert len(list(room.walls())) == len(room.vertices)
