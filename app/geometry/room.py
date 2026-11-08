"""Room floor plan as an irregular, editable polygon (port of the Node src/domain/Room.ts).

Coordinates are meters. The polygon lives in the floor plane: its x is the room's x axis and its y is
the room's z axis (depth); the room's height runs along the 3D y axis, which points up.

Doors and windows are not ported: nothing persists them yet, so the clear-path check is skipped.

Los vértices de Room se guardan en un anillo (CircularDoublyLinkedList de app.structures): la pared i va
del vértice i al siguiente, y la siguiente del último vértice es el primero sin calcular ningún módulo.
"""

import math
from dataclasses import dataclass

from app.structures import CircularDoublyLinkedList

# Polygons with a smaller area are treated as degenerate (collinear or collapsed vertices).
MIN_AREA_M2 = 0.01

# Slack for the orientation test, in m². Cross products of room-scale coordinates are exact to
# ~1e-15, so 1e-12 only absorbs rounding while still telling apart points a micrometer off a line.
_COLLINEAR_EPSILON = 1e-12


class RoomError(ValueError):
    """The requested shape is not a valid room (the API answers 422)."""


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class BoundingBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


def _to_point(value):
    if isinstance(value, Point):
        point = value
    elif isinstance(value, dict):
        point = Point(float(value["x"]), float(value["y"]))
    else:
        x, y = value
        point = Point(float(x), float(y))
    if not (math.isfinite(point.x) and math.isfinite(point.y)):
        raise RoomError("vertex coordinates must be finite numbers")
    return point


def _signed_area(vertices):
    """Shoelace formula: positive for counter-clockwise vertices, negative for clockwise."""
    return _signed_area_of_edges((a, vertices[(i + 1) % len(vertices)]) for i, a in enumerate(vertices))


def _signed_area_of_edges(edges):
    """Fórmula del área con signo a partir de las aristas (a, b) en orden, cerrando el polígono. O(n).

    Suma en el mismo orden que _signed_area, así que el resultado es idéntico bit a bit.
    """
    total = 0.0
    for a, b in edges:
        total += a.x * b.y - b.x * a.y
    return total / 2


def _ring_of(points):
    """Un anillo nuevo con los puntos en orden: el índice 0 es la cabeza. O(n)."""
    ring = CircularDoublyLinkedList()
    for point in points:
        ring.append(point)
    return ring


def _orientation(p, q, r):
    """Sign of the cross product (q - p) x (r - p): 1 left turn, -1 right turn, 0 collinear."""
    cross = (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
    if abs(cross) <= _COLLINEAR_EPSILON:
        return 0
    return 1 if cross > 0 else -1


def _on_segment(p, q, r):
    """For collinear p, q, r: whether r lies on the closed segment pq."""
    return (
        min(p.x, q.x) - 1e-12 <= r.x <= max(p.x, q.x) + 1e-12
        and min(p.y, q.y) - 1e-12 <= r.y <= max(p.y, q.y) + 1e-12
    )


def _segments_touch(a, b, c, d):
    """Whether the closed segments ab and cd share at least one point (crossing or touching)."""
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4):
        return True
    return (
        (o1 == 0 and _on_segment(a, b, c))
        or (o2 == 0 and _on_segment(a, b, d))
        or (o3 == 0 and _on_segment(c, d, a))
        or (o4 == 0 and _on_segment(c, d, b))
    )


def _segment_crosses_open_rectangle(a, b, min_x, min_y, max_x, max_y):
    """Whether segment ab passes through the interior of the rectangle (its boundary does not count).

    Liang-Barsky clipping keeps the part of the segment inside the closed rectangle. That part lies
    either on one side of the rectangle (touching only) or, except for its endpoints, in the interior,
    so testing its midpoint is enough.
    """
    dx, dy = b.x - a.x, b.y - a.y
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a.x - min_x), (dx, max_x - a.x), (-dy, a.y - min_y), (dy, max_y - a.y)):
        if p == 0:
            if q < 0:
                return False  # parallel to this side and outside it
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    mid_x = a.x + dx * (t0 + t1) / 2
    mid_y = a.y + dy * (t0 + t1) / 2
    eps = 1e-12
    return min_x + eps < mid_x < max_x - eps and min_y + eps < mid_y < max_y - eps


def _without_repeated_vertices(vertices):
    """Drop consecutive duplicates (including last == first): they form zero-length edges only."""
    ring = []
    for point in vertices:
        if not ring or point != ring[-1]:
            ring.append(point)
    while len(ring) > 1 and ring[-1] == ring[0]:
        ring.pop()
    return ring


def _find_self_intersection(vertices):
    """Return a description of the first self-intersection, or None if the polygon is simple.

    Zero-length edges (repeated consecutive vertices) are ignored. Two non-adjacent edges must not
    share any point, so a vertex resting on a non-adjacent edge is rejected too. Adjacent edges only
    share their common vertex; if they fold back over each other (collinear, opposite direction),
    that is also an intersection.
    """
    ring = _without_repeated_vertices(vertices)
    n = len(ring)
    edges = [(ring[i], ring[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        a, b = edges[i]
        # The next edge b -> c shares vertex b; it folds back over this one if it is collinear and
        # points the opposite way. Collinear and the same way is just a redundant vertex.
        c = edges[(i + 1) % n][1]
        backwards = (b.x - a.x) * (c.x - b.x) + (b.y - a.y) * (c.y - b.y) < 0
        if _orientation(a, b, c) == 0 and backwards:
            return f"edges {i} and {(i + 1) % n} overlap"
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # edges 0 and n-1 are adjacent through vertex 0
            if _segments_touch(a, b, *edges[j]):
                return f"edges {i} and {j} intersect"
    return None


def _validate(vertices):
    if len(vertices) < 3:
        raise RoomError(f"a room needs at least 3 vertices to form a polygon (received: {len(vertices)})")
    if abs(_signed_area(vertices)) < MIN_AREA_M2:
        raise RoomError(f"the vertices do not form a valid polygon (area below {MIN_AREA_M2} m²)")
    problem = _find_self_intersection(vertices)
    if problem:
        raise RoomError(f"the polygon intersects itself ({problem})")


def _validate_height(height_m):
    if not (math.isfinite(height_m) and height_m > 0):
        raise RoomError(f"height_m must be a finite number greater than 0 (received: {height_m})")


class Room:
    """Irregular polygon (not necessarily a rectangle) at real metric scale, editable after creation.

    Every mutation validates the new shape before replacing the current one, so a rejected edit
    leaves the room unchanged.

    Los vértices viven en un anillo (CircularDoublyLinkedList); la pared i es (vértice i, siguiente).
    Costes (n = vértices):
        vertices, vertices_json, area_m2, bounding_box, walls, contains_point: O(n).
        contains_rectangle: O(n). neighbor_walls(i): O(min(i, n - i)).
        move_vertex, insert_vertex, remove_vertex: O(min(i, n - i)) para llegar al nodo y cambiarlo en
            O(1), más O(n²) de la validación del polígono (comprobar que no se cruza). set_vertices: O(n²).
    Una edición rechazada deshace el cambio en el anillo antes de lanzar el error.
    """

    def __init__(self, vertices, height_m):
        points = [_to_point(v) for v in vertices]
        height_m = float(height_m)
        _validate(points)
        _validate_height(height_m)
        self._ring = _ring_of(points)
        self._height_m = height_m

    @classmethod
    def from_record(cls, vertices, ancho, profundidad, alto):
        """Room stored in the habitaciones table.

        Rooms created by /analyze have no vertices: they are the rectangle [0, ancho] x [0, profundidad].
        """
        if vertices is None:
            vertices = [(0, 0), (ancho, 0), (ancho, profundidad), (0, profundidad)]
        return cls(vertices, alto)

    def to_record(self):
        """Column values for the habitaciones table: ancho/profundidad are the bounding box."""
        box = self.bounding_box()
        return {
            "vertices": self.vertices_json(),
            "ancho": box.max_x - box.min_x,
            "profundidad": box.max_y - box.min_y,
            "alto": self._height_m,
        }

    @property
    def vertices(self):
        return tuple(self._ring)

    @property
    def height_m(self):
        return self._height_m

    def vertices_json(self):
        return [{"x": p.x, "y": p.y} for p in self._ring]

    def area_m2(self):
        return abs(_signed_area_of_edges(self._ring.pairs()))

    def bounding_box(self):
        xs = [p.x for p in self._ring]
        ys = [p.y for p in self._ring]
        return BoundingBox(min(xs), min(ys), max(xs), max(ys))

    def walls(self):
        """Las paredes (inicio, fin) en orden, de la 0 a la última, que cierra con el vértice 0. O(n)."""
        return self._ring.pairs()

    def neighbor_walls(self, index):
        """(pared anterior, pared siguiente) de la pared index, dando la vuelta en los extremos.

        La pared index va del vértice index al siguiente; la anterior acaba en el vértice index y la
        siguiente empieza en el vértice index + 1. O(min(i, n - i)) para llegar al nodo y O(1) después.

        Raises:
            RoomError: index fuera de rango.
        """
        self._check_index(index, "neighbor_walls")
        node = self._ring.node_at(index)
        return (node.prev.value, node.value), (node.next.value, node.next.next.value)

    def contains_point(self, x, y):
        """Point-in-polygon by ray casting, so callers respect the real shape, not its bounding box."""
        inside = False
        # Cada pared (vj, vi) del anillo; el orden de las aristas no cambia el resultado (solo alterna).
        for vj, vi in self._ring.pairs():
            if (vi.y > y) != (vj.y > y) and x < (vj.x - vi.x) * (y - vi.y) / (vj.y - vi.y) + vi.x:
                inside = not inside
        return inside

    def contains_rectangle(self, x, y, width, depth):
        """Whether the axis-aligned rectangle [x, x + width] x [y, y + depth] lies inside the polygon.

        The four corners being inside is not enough in a concave room: a long rectangle can have all
        its corners in the floor and still cross a notch between them. If no wall passes through the
        rectangle's interior, that interior is either entirely inside the room or entirely outside, so
        its center decides. Walls along its edges (a piece pushed against a wall) do not count, and
        unlike a corner test, the center is never on a wall, where ray casting is ambiguous.
        """
        if any(
            _segment_crosses_open_rectangle(a, b, x, y, x + width, y + depth) for a, b in self._ring.pairs()
        ):
            return False
        return self.contains_point(x + width / 2, y + depth / 2)

    # --- Mutations: the user can edit the room surface after the scan ---------------------------

    def _check_index(self, index, operation):
        if not 0 <= index < len(self._ring):
            raise RoomError(f"{operation}: index {index} is out of range (0-{len(self._ring) - 1})")

    def _validate_or_undo(self, undo):
        """Valida la forma actual del anillo; si no vale, llama a undo() y relanza el error."""
        try:
            _validate(list(self._ring))
        except Exception:
            undo()
            raise

    def set_vertices(self, vertices):
        """Replace the whole shape at once (re-scan or full manual redraw)."""
        points = [_to_point(v) for v in vertices]
        _validate(points)
        self._ring = _ring_of(points)

    def move_vertex(self, index, point):
        """Move one vertex (drag-to-edit in the UI) without touching the others."""
        self._check_index(index, "move_vertex")
        point = _to_point(point)
        node = self._ring.node_at(index)
        old = node.value
        node.value = point

        def undo():
            node.value = old

        self._validate_or_undo(undo)

    def insert_vertex(self, after_index, point):
        """Insert a vertex between after_index and the next one (splits an edge)."""
        self._check_index(after_index, "insert_vertex")
        point = _to_point(point)
        # Tras el último vértice, el nodo nuevo queda al final (antes de la cabeza): la pared que cierra.
        node = self._ring.insert_after(self._ring.node_at(after_index), point)
        self._validate_or_undo(lambda: self._ring.remove(node))

    def remove_vertex(self, index):
        """Remove a vertex (simplifies the shape); a room never drops below a triangle."""
        if len(self._ring) <= 3:
            raise RoomError("remove_vertex: a room must keep at least 3 vertices")
        self._check_index(index, "remove_vertex")
        node = self._ring.node_at(index)
        was_head, before = node is self._ring.head, node.prev
        value = self._ring.remove(node)

        def undo():
            # Vuelve a su sitio: como cabeza si lo era, si no detrás de su vértice anterior.
            if was_head:
                self._ring.insert(0, value)
            else:
                self._ring.insert_after(before, value)

        self._validate_or_undo(undo)
