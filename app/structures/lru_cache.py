"""Caché LRU (menos usado recientemente) sobre DoublyLinkedList + dict."""

from collections.abc import Hashable, Iterator
from typing import Generic, TypeVar

from app.structures.doubly_linked_list import DoublyLinkedList, DoublyNode
from app.structures.exceptions import InvariantError, validate_capacity

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LruCache(Generic[K, V]):
    """Caché de tamaño acotado que, al llenarse, expulsa la entrada usada hace más tiempo.

    Dos piezas:
        - un dict clave -> nodo, para encontrar una entrada en O(1);
        - una DoublyLinkedList de pares (clave, valor) ordenada por uso: el frente es lo más reciente
          y el final lo más antiguo. Con el nodo en la mano, mover al frente y quitar son O(1).

    Costes (n = entradas):
        get, put, __contains__, peek, discard, len: O(1) (promedio del dict).
        Expulsar el menos usado al insertar con la caché llena: O(1).
        Recorrer (keys, items), clear: O(n). check_invariants: O(n).
    Memoria: O(capacity), un nodo y una entrada del dict por clave.

    get cuenta aciertos (hits) y fallos (misses); peek y __contains__ consultan sin contar ni
    reordenar. No es segura entre hilos: está pensada para el bucle de eventos de asyncio, donde
    entre dos líneas sin await no se ejecuta otra corrutina.
    """

    def __init__(self, capacity: int):
        """
        Raises:
            ValueError: capacity no es un entero mayor que 0 (una LRU sin límite no tiene sentido).
        """
        if capacity is None:
            raise ValueError("capacity debe ser un entero mayor que 0")
        self._capacity = validate_capacity(capacity)
        self._nodes: dict[K, DoublyNode[tuple[K, V]]] = {}
        self._order: DoublyLinkedList[tuple[K, V]] = DoublyLinkedList(capacity)
        self._hits = 0
        self._misses = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def hits(self) -> int:
        """Llamadas a get que encontraron la clave."""
        return self._hits

    @property
    def misses(self) -> int:
        """Llamadas a get que no la encontraron."""
        return self._misses

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, key: K) -> bool:
        """Si la clave está, sin marcarla como usada ni contar. O(1)."""
        return key in self._nodes

    def get(self, key: K, default: V | None = None) -> V | None:
        """El valor de key (y la marca como recién usada), o default si no está. O(1).

        Suma un acierto o un fallo a los contadores.
        """
        node = self._nodes.get(key)
        if node is None:
            self._misses += 1
            return default
        self._hits += 1
        self._order.move_to_front(node)
        return node.value[1]

    def peek(self, key: K, default: V | None = None) -> V | None:
        """El valor de key sin marcarla como usada ni contar, o default. O(1)."""
        node = self._nodes.get(key)
        return default if node is None else node.value[1]

    def put(self, key: K, value: V) -> None:
        """Guarda value en key y la marca como recién usada. O(1).

        Si la clave ya estaba, reemplaza su valor. Si es nueva y la caché está llena, antes expulsa
        la entrada menos usada (la del final de la lista).
        """
        node = self._nodes.get(key)
        if node is not None:
            node.value = (key, value)
            self._order.move_to_front(node)
            return
        if len(self._nodes) >= self._capacity:
            evicted_key, _ = self._order.pop_back()
            del self._nodes[evicted_key]
        self._nodes[key] = self._order.push_front((key, value))

    def discard(self, key: K) -> bool:
        """Quita key si está. Devuelve si estaba. O(1)."""
        node = self._nodes.pop(key, None)
        if node is None:
            return False
        self._order.remove(node)
        return True

    def clear(self) -> None:
        """Vacía la caché y pone a cero los contadores. O(n)."""
        self._order.clear()
        self._nodes.clear()
        self._hits = 0
        self._misses = 0

    def keys(self) -> Iterator[K]:
        """Claves de la más reciente a la menos usada. O(n)."""
        for key, _ in self._order:
            yield key

    def items(self) -> Iterator[tuple[K, V]]:
        """Pares (clave, valor) de la más reciente a la menos usada. O(n)."""
        yield from self._order

    def __repr__(self) -> str:
        return (
            f"LruCache(size={len(self)}, capacity={self._capacity}, hits={self._hits}, misses={self._misses})"
        )

    def check_invariants(self) -> None:
        """Comprueba que el dict y la lista describen las mismas entradas. O(n).

        Raises:
            InvariantError: la lista es inconsistente, los tamaños no coinciden, se superó la
                capacidad, o una clave del dict no apunta a un nodo de la lista con esa misma clave.
        """
        self._order.check_invariants()
        if len(self._order) != len(self._nodes):
            raise InvariantError(f"la lista tiene {len(self._order)} nodos y el dict {len(self._nodes)}")
        if len(self._nodes) > self._capacity:
            raise InvariantError("se superó la capacidad")
        if self._hits < 0 or self._misses < 0:
            raise InvariantError("contadores negativos")
        for key, node in self._nodes.items():
            if node.owner is not self._order or node.value[0] != key:
                raise InvariantError(f"la clave {key!r} no apunta a su nodo")
