"""Planta de la habitación como polígono irregular y editable (port de src/domain/Room.ts del Node).

Las coordenadas están en metros. El polígono vive en el plano del suelo: su x es el eje x de la sala y su y
es el eje z de la sala (la profundidad); la altura de la sala va por el eje y del 3D, que apunta hacia arriba.

Puertas y ventanas no se portan: todavía no se guardan en ningún sitio, así que se omite la comprobación
del paso libre.

Los vértices de Room se guardan en un anillo (CircularDoublyLinkedList de app.structures): la pared i va
del vértice i al siguiente, y la siguiente del último vértice es el primero sin calcular ningún módulo.
"""

import math
from dataclasses import dataclass

from app.structures import CircularDoublyLinkedList

# Los polígonos con un área menor se consideran degenerados (vértices colineales o colapsados).
MIN_AREA_M2 = 0.01

# Holgura de la prueba de orientación, en m². Los productos cruzados con coordenadas de una sala son exactos
# hasta ~1e-15, así que 1e-12 solo absorbe el redondeo y aún distingue puntos a un micrómetro de una recta.
_COLLINEAR_EPSILON = 1e-12


class RoomError(ValueError):
    """La forma pedida no es una habitación válida (la API responde 422)."""


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
    """Fórmula del área de Gauss (shoelace): positiva en sentido antihorario, negativa en horario."""
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
    """Signo del producto cruzado (q - p) x (r - p): 1 giro a la izquierda, -1 a la derecha, 0 colineales."""
    cross = (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
    if abs(cross) <= _COLLINEAR_EPSILON:
        return 0
    return 1 if cross > 0 else -1


def _on_segment(p, q, r):
    """Con p, q y r colineales: si r está en el segmento cerrado pq."""
    return (
        min(p.x, q.x) - 1e-12 <= r.x <= max(p.x, q.x) + 1e-12
        and min(p.y, q.y) - 1e-12 <= r.y <= max(p.y, q.y) + 1e-12
    )


def _segments_touch(a, b, c, d):
    """Si los segmentos cerrados ab y cd comparten al menos un punto (se cruzan o se tocan)."""
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
    """Si el segmento ab pasa por el interior del rectángulo (su borde no cuenta).

    El recorte de Liang-Barsky se queda con la parte del segmento dentro del rectángulo cerrado. Esa parte
    está o bien sobre un lado del rectángulo (solo lo toca) o bien, salvo sus extremos, en el interior, así
    que basta con probar su punto medio.
    """
    dx, dy = b.x - a.x, b.y - a.y
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a.x - min_x), (dx, max_x - a.x), (-dy, a.y - min_y), (dy, max_y - a.y)):
        if p == 0:
            if q < 0:
                return False  # paralelo a este lado y fuera de él
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
    """Quita los duplicados consecutivos (también último == primero): solo forman aristas de longitud 0."""
    ring = []
    for point in vertices:
        if not ring or point != ring[-1]:
            ring.append(point)
    while len(ring) > 1 and ring[-1] == ring[0]:
        ring.pop()
    return ring


def _find_self_intersection(vertices):
    """Devuelve la descripción de la primera autointersección, o None si el polígono es simple.

    Las aristas de longitud 0 (vértices consecutivos repetidos) se ignoran. Dos aristas no adyacentes no
    pueden compartir ningún punto, así que también se rechaza un vértice apoyado en una arista no adyacente.
    Las aristas adyacentes solo comparten su vértice común; si se doblan una sobre otra (colineales y en
    sentido contrario), eso también es una intersección.
    """
    ring = _without_repeated_vertices(vertices)
    n = len(ring)
    edges = [(ring[i], ring[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        a, b = edges[i]
        # La arista siguiente b -> c comparte el vértice b; se dobla sobre esta si es colineal y va en
        # sentido contrario. Colineal y en el mismo sentido es solo un vértice redundante.
        c = edges[(i + 1) % n][1]
        backwards = (b.x - a.x) * (c.x - b.x) + (b.y - a.y) * (c.y - b.y) < 0
        if _orientation(a, b, c) == 0 and backwards:
            return f"edges {i} and {(i + 1) % n} overlap"
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # las aristas 0 y n-1 son adyacentes por el vértice 0
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
    """Polígono irregular (no necesariamente un rectángulo) a escala métrica real, editable tras crearlo.

    Cada cambio valida la forma nueva antes de sustituir la actual, así que una edición rechazada deja la
    sala sin cambios.

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
        """Sala guardada en la tabla habitaciones.

        Las salas creadas por /analyze no tienen vértices: son el rectángulo [0, ancho] x [0, profundidad].
        """
        if vertices is None:
            vertices = [(0, 0), (ancho, 0), (ancho, profundidad), (0, profundidad)]
        return cls(vertices, alto)

    def to_record(self):
        """Valores de las columnas de la tabla habitaciones: ancho y profundidad son la caja delimitadora."""
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
        """Punto en polígono por lanzamiento de rayos: respeta la forma real, no su caja delimitadora."""
        inside = False
        # Cada pared (vj, vi) del anillo; el orden de las aristas no cambia el resultado (solo alterna).
        for vj, vi in self._ring.pairs():
            if (vi.y > y) != (vj.y > y) and x < (vj.x - vi.x) * (y - vi.y) / (vj.y - vi.y) + vi.x:
                inside = not inside
        return inside

    def contains_rectangle(self, x, y, width, depth):
        """Si el rectángulo alineado con los ejes [x, x + width] x [y, y + depth] está dentro del polígono.

        Que las cuatro esquinas estén dentro no basta en una sala cóncava: un rectángulo largo puede tener
        todas sus esquinas en el suelo y aun así cruzar una muesca entre ellas. Si ninguna pared pasa por el
        interior del rectángulo, ese interior está o todo dentro de la sala o todo fuera, así que decide su
        centro. Las paredes a lo largo de sus lados (un mueble pegado a una pared) no cuentan y, a diferencia
        de una prueba por esquinas, el centro nunca está sobre una pared, donde el lanzamiento de rayos es
        ambiguo.
        """
        if any(
            _segment_crosses_open_rectangle(a, b, x, y, x + width, y + depth) for a, b in self._ring.pairs()
        ):
            return False
        return self.contains_point(x + width / 2, y + depth / 2)

    # --- Cambios: el usuario puede editar la superficie de la sala después del escaneo ----------

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
        """Sustituye toda la forma de una vez (nuevo escaneo o redibujado manual completo)."""
        points = [_to_point(v) for v in vertices]
        _validate(points)
        self._ring = _ring_of(points)

    def move_vertex(self, index, point):
        """Mueve un vértice (arrastrar para editar en la interfaz) sin tocar los demás."""
        self._check_index(index, "move_vertex")
        point = _to_point(point)
        node = self._ring.node_at(index)
        old = node.value
        node.value = point

        def undo():
            node.value = old

        self._validate_or_undo(undo)

    def insert_vertex(self, after_index, point):
        """Inserta un vértice entre after_index y el siguiente (parte una arista en dos)."""
        self._check_index(after_index, "insert_vertex")
        point = _to_point(point)
        # Tras el último vértice, el nodo nuevo queda al final (antes de la cabeza): la pared que cierra.
        node = self._ring.insert_after(self._ring.node_at(after_index), point)
        self._validate_or_undo(lambda: self._ring.remove(node))

    def remove_vertex(self, index):
        """Quita un vértice (simplifica la forma); una sala nunca baja de un triángulo."""
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
