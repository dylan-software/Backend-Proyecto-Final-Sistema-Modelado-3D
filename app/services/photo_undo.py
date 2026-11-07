"""Deshacer la foto a 3D: copias de cómo estaba cada sala antes de cada foto (CONTRATO-DESHACER-FOTO.md).

Cada sala tiene una LinkedStack de copias (RoomSnapshot), la más reciente en la cima, con capacidad
MAX_UNDO_DEPTH. Las pilas viven en una LruCache de MAX_UNDO_ROOMS salas con clave (user_id, room_id),
así la memoria queda acotada aunque haya muchas salas.

Todo vive en memoria del proceso: se pierde si el servidor se reinicia y no se comparte entre workers
(igual que los límites de peticiones, decisión 24). No es seguro entre hilos: está pensado para el
bucle de eventos de asyncio, sin await dentro de ninguna operación.
"""

from dataclasses import dataclass

from app.models.space import MuebleSugerido
from app.structures import LinkedStack, LruCache

# Salas cuyo historial se recuerda; al pasar de aquí se expulsa la usada hace más tiempo.
MAX_UNDO_ROOMS = 200
# Fotos que se pueden deshacer por sala; con la pila llena se descarta la copia más antigua.
MAX_UNDO_DEPTH = 5


@dataclass(frozen=True, slots=True)
class FurnitureSnapshot:
    """Copia de un mueble de muebles_sugeridos (metros; posicion_x/z = centro, posicion_y = base)."""

    nombre: str
    ancho: float
    alto: float
    profundidad: float
    posicion_x: float
    posicion_y: float
    posicion_z: float


@dataclass(frozen=True, slots=True)
class RoomSnapshot:
    """Copia inmutable de lo que una foto sobrescribe en una sala.

    vertices es una tupla de pares (x, y), o None si la sala no tenía forma (las de /analyze).
    El nombre y la descripción no entran: la foto no los cambia.
    """

    vertices: tuple[tuple[float, float], ...] | None
    ancho: float
    profundidad: float
    alto: float
    furniture: tuple[FurnitureSnapshot, ...]


def snapshot_room(row):
    """Copia de las columnas que sobrescribe la foto y de los muebles de row (con muebles ya cargados).

    No guarda referencias a filas de SQLAlchemy: solo valores.
    """
    vertices = None if row.vertices is None else tuple((v["x"], v["y"]) for v in row.vertices)
    furniture = tuple(
        FurnitureSnapshot(
            nombre=piece.nombre,
            ancho=piece.ancho,
            alto=piece.alto,
            profundidad=piece.profundidad,
            posicion_x=piece.posicion_x,
            posicion_y=piece.posicion_y,
            posicion_z=piece.posicion_z,
        )
        for piece in sorted(row.muebles, key=lambda piece: piece.id or 0)
    )
    return RoomSnapshot(vertices, row.ancho, row.profundidad, row.alto, furniture)


def restore_room(row, snapshot):
    """Vuelve a poner en row la forma y los muebles de snapshot (falta el commit).

    Asigna una lista de vértices NUEVA (decisión 5) y una lista nueva de muebles: la cascada
    delete-orphan borra los actuales en el mismo commit.
    """
    row.vertices = None if snapshot.vertices is None else [{"x": x, "y": y} for x, y in snapshot.vertices]
    row.ancho = snapshot.ancho
    row.profundidad = snapshot.profundidad
    row.alto = snapshot.alto
    row.muebles = [
        MuebleSugerido(
            nombre=piece.nombre,
            ancho=piece.ancho,
            alto=piece.alto,
            profundidad=piece.profundidad,
            posicion_x=piece.posicion_x,
            posicion_y=piece.posicion_y,
            posicion_z=piece.posicion_z,
        )
        for piece in snapshot.furniture
    ]


def _drop_oldest(stack):
    """Quita la copia del fondo de stack (la más antigua) usando una segunda pila. O(n), n <= capacidad.

    Se pasa todo a la auxiliar (el fondo queda en su cima), se descarta esa cima y se devuelve el resto
    en el mismo orden. Así LinkedStack no necesita ninguna operación nueva.
    """
    spare = LinkedStack(stack.capacity)
    while stack:
        spare.push(stack.pop())
    spare.pop()
    while spare:
        stack.push(spare.pop())


class PhotoUndoStore:
    """Pilas de copias por (user_id, room_id), dentro de una LruCache.

    Costes: push, pop y depth O(1) (push O(depth) cuando la pila está llena y descarta la más antigua).
    Memoria: como mucho max_rooms * depth copias.
    """

    def __init__(self, max_rooms=MAX_UNDO_ROOMS, depth=MAX_UNDO_DEPTH):
        self._rooms: LruCache[tuple[str, int], LinkedStack[RoomSnapshot]] = LruCache(max_rooms)
        self._depth = depth

    @property
    def max_rooms(self):
        return self._rooms.capacity

    @property
    def max_depth(self):
        return self._depth

    def __len__(self):
        """Salas con historial."""
        return len(self._rooms)

    def push(self, user_id, room_id, snapshot):
        """Apila snapshot en la sala; con la pila llena, antes descarta la copia más antigua."""
        key = (user_id, room_id)
        stack = self._rooms.get(key)
        if stack is None:
            stack = LinkedStack(self._depth)
            self._rooms.put(key, stack)
        if stack.is_full():
            _drop_oldest(stack)
        stack.push(snapshot)

    def pop(self, user_id, room_id):
        """Desapila y devuelve la copia más reciente de la sala, o None si no hay ninguna.

        Sin await entre leer la pila y quitar la copia. Una pila que queda vacía sale de la caché.
        """
        key = (user_id, room_id)
        stack = self._rooms.get(key)
        if not stack:
            return None
        snapshot = stack.pop()
        if stack.is_empty():
            self._rooms.discard(key)
        return snapshot

    def depth(self, user_id, room_id):
        """Cuántas fotos se pueden deshacer en la sala (sin marcarla como usada)."""
        stack = self._rooms.peek((user_id, room_id))
        return len(stack) if stack else 0

    def clear(self):
        """Olvida todo el historial (lo mismo que un reinicio del servidor)."""
        self._rooms.clear()


# El almacén del proceso; los endpoints lo reciben con la dependencia get_photo_undo_store.
photo_undo_store = PhotoUndoStore()
