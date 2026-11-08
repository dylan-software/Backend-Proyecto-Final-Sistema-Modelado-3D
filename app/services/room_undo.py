"""Deshacer de una sala: UNA pila de copias por sala para todos sus cambios (CONTRATO-FASE2.md, sección 1).

Antes de cada cambio guardado de la forma o de los objetos de una sala (foto a 3D, mensaje del chat con
acciones, POST/PATCH/DELETE de objetos, layout y vértices), se apila una copia (RoomSnapshot) de cómo
estaba. Deshacer siempre quita la copia más reciente, sea del cambio que sea (LIFO), así que deshacer la
foto nunca borra trabajo posterior: antes hay que deshacer ese trabajo.

Cada sala tiene una LinkedStack de capacidad MAX_UNDO_DEPTH; las pilas viven en una LruCache de
MAX_UNDO_ROOMS salas con clave (user_id, room_id). Con la pila llena, o si las copias de la sala suman más
de MAX_UNDO_PIECES objetos, se descartan las copias más antiguas (la justificación está en
docs/DEFENSE.md). Así la memoria queda acotada aunque haya muchas salas y muchos objetos.

Todo vive en memoria del proceso: se pierde si el servidor se reinicia y no se comparte entre workers
(igual que los límites de peticiones, decisión 24). No es seguro entre hilos: está pensado para el
bucle de eventos de asyncio, sin await dentro de ninguna operación del almacén.
"""

from dataclasses import dataclass

from app.models.space import MuebleSugerido
from app.routers.errors import DATABASE_ERRORS, database_error
from app.structures import LinkedStack, LruCache

# Salas cuyo historial se recuerda; al pasar de aquí se expulsa la usada hace más tiempo.
MAX_UNDO_ROOMS = 100
# Cambios que se pueden deshacer por sala; con la pila llena se descarta la copia más antigua.
MAX_UNDO_DEPTH = 50
# Objetos guardados como mucho entre todas las copias de una sala (una copia sin objetos cuenta 1).
MAX_UNDO_PIECES = 2000

# Qué cambio se hizo después de cada copia (lo que deshacerla revierte).
CHANGES = ("photo", "chat", "add", "update", "delete", "layout", "vertices")


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
    """Copia inmutable de la forma y los objetos de una sala justo antes de un cambio.

    vertices es una tupla de pares (x, y), o None si la sala no tenía forma (las de /analyze).
    change es el cambio que vino después (uno de CHANGES). El nombre y la descripción no entran:
    ningún cambio con Deshacer los toca.
    """

    vertices: tuple[tuple[float, float], ...] | None
    ancho: float
    profundidad: float
    alto: float
    furniture: tuple[FurnitureSnapshot, ...]
    change: str


def snapshot_room(row, change):
    """Copia de la forma y de los muebles de row (con muebles ya cargados) antes del cambio change.

    No guarda referencias a filas de SQLAlchemy: solo valores.

    Raises:
        ValueError: change no es uno de CHANGES.
    """
    if change not in CHANGES:
        raise ValueError(f"cambio desconocido: {change}")
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
    return RoomSnapshot(vertices, row.ancho, row.profundidad, row.alto, furniture, change)


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


def _weight(snapshot):
    """Lo que una copia cuenta para MAX_UNDO_PIECES: sus objetos, y 1 si no tiene ninguno."""
    return max(1, len(snapshot.furniture))


def _drop_oldest(stack):
    """Quita y devuelve la copia del fondo de stack (la más antigua) usando una segunda pila.

    O(n), n <= capacidad. Se pasa todo a la auxiliar (el fondo queda en su cima), se quita esa cima y
    se devuelve el resto en el mismo orden. Así LinkedStack no necesita ninguna operación nueva.
    """
    spare = LinkedStack(stack.capacity)
    while stack:
        spare.push(stack.pop())
    oldest = spare.pop()
    while spare:
        stack.push(spare.pop())
    return oldest


class RoomUndoStore:
    """Pilas de copias por (user_id, room_id), dentro de una LruCache.

    Costes (d = copias de la sala, d <= max_depth): pop y depth O(1); push O(d), porque suma los
    objetos guardados para respetar max_pieces y, si hay que descartar, cada descarte cuesta O(d).
    Memoria: como mucho max_rooms * max_pieces objetos copiados.
    """

    def __init__(self, max_rooms=MAX_UNDO_ROOMS, depth=MAX_UNDO_DEPTH, max_pieces=MAX_UNDO_PIECES):
        self._rooms: LruCache[tuple[str, int], LinkedStack[RoomSnapshot]] = LruCache(max_rooms)
        self._depth = depth
        self._max_pieces = max_pieces

    @property
    def max_rooms(self):
        return self._rooms.capacity

    @property
    def max_depth(self):
        return self._depth

    @property
    def max_pieces(self):
        return self._max_pieces

    def __len__(self):
        """Salas con historial."""
        return len(self._rooms)

    def push(self, user_id, room_id, snapshot):
        """Apila snapshot en la sala.

        Con la pila llena descarta antes la copia más antigua, y después sigue descartando las más
        antiguas mientras las copias de la sala (la nueva incluida) sumen más de max_pieces objetos.
        La copia nueva siempre se guarda.
        """
        key = (user_id, room_id)
        stack = self._rooms.get(key)
        if stack is None:
            stack = LinkedStack(self._depth)
            self._rooms.put(key, stack)
        if stack.is_full():
            _drop_oldest(stack)
        pieces = _weight(snapshot) + sum(_weight(copy) for copy in stack)
        while stack and pieces > self._max_pieces:
            pieces -= _weight(_drop_oldest(stack))
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
        """Cuántos cambios se pueden deshacer en la sala (sin marcarla como usada)."""
        stack = self._rooms.peek((user_id, room_id))
        return len(stack) if stack else 0

    def forget(self, user_id, room_id):
        """Olvida el historial de la sala. O(1)."""
        self._rooms.discard((user_id, room_id))

    def clear(self):
        """Olvida todo el historial (lo mismo que un reinicio del servidor)."""
        self._rooms.clear()


async def commit_with_undo(session, row, store, before):
    """Guarda los cambios de row y, si el commit sale bien, apila before en su sala.

    Es el único punto donde se guarda un cambio con Deshacer: lo usan la foto, los objetos, el chat,
    el layout y los vértices. Si el commit falla, hace rollback, no apila nada y lanza el 503/500 de
    siempre (con request_id). Con store None solo guarda (servicios usados sin Deshacer).

    Args:
        row: la Habitacion cambiada (de la cuenta que hace la petición).
        before: snapshot_room(row, ...) tomado ANTES de cambiar row.
    """
    # Se leen antes del commit: así no hace falta recargar la fila después.
    user_id, room_id = row.user_id, row.id
    try:
        await session.commit()
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_error(exc) from exc
    if store is not None:
        store.push(user_id, room_id, before)


# El almacén del proceso; los endpoints lo reciben con la dependencia get_room_undo_store.
room_undo_store = RoomUndoStore()


def get_room_undo_store():
    """Dependencia de FastAPI con el almacén de Deshacer; las pruebas usan uno limpio.

    Vive aquí (no en un router) para que la usen app.routers.rooms, app.routers.spaces y
    app.routers.chat sin importaciones circulares.
    """
    return room_undo_store
