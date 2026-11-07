"""Lista circular doblemente enlazada: un anillo de nodos sin principio ni final (paredes de Room)."""

from collections.abc import Iterator
from typing import Generic, TypeVar

from app.structures.exceptions import CapacityError, InvariantError, validate_capacity

T = TypeVar("T")


class RingNode(Generic[T]):
    """Un nodo del anillo: su valor, el anterior, el siguiente y su anillo (None si no está en ninguno).

    Dentro de un anillo, prev y next nunca son None: con un solo nodo, ambos apuntan a él mismo.
    """

    __slots__ = ("next", "owner", "prev", "value")

    def __init__(self, value: T):
        self.value = value
        self.prev: RingNode[T] | None = None
        self.next: RingNode[T] | None = None
        self.owner: CircularDoublyLinkedList[T] | None = None


class CircularDoublyLinkedList(Generic[T]):
    """Anillo doblemente enlazado con un puntero a la cabeza (el índice 0).

    El último nodo enlaza con la cabeza (head.prev es el último), así que "el siguiente del último" es el
    primero sin calcular ningún módulo. Los índices cuentan desde la cabeza, en [0, size); los negativos NO
    cuentan desde el final. Borrar el índice 0 se comporta como borrar el primero de una lista: la cabeza
    pasa al siguiente.

    Costes (n = elementos, i = índice):
        append, insert_after(nodo), remove(nodo), len, head: O(1).
        get, set, a[i], a[i] = v, node_at, insert, remove_at: O(min(i, n - i)) (camina por el lado corto).
        rotate(k): O(min(k mod n, n - k mod n)).
        Recorrer (iter, reversed, pairs), clear: O(n). check_invariants: O(n).
    Memoria: O(n), un nodo por elemento.

    capacity limita los elementos (None = sin límite); insertar con el anillo lleno lanza CapacityError.
    """

    def __init__(self, capacity: int | None = None):
        self._capacity = validate_capacity(capacity)
        self._head: RingNode[T] | None = None
        self._size = 0

    @property
    def capacity(self) -> int | None:
        return self._capacity

    @property
    def head(self) -> RingNode[T] | None:
        """El nodo del índice 0 (o None si el anillo está vacío). O(1)."""
        return self._head

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def is_empty(self) -> bool:
        return self._size == 0

    def is_full(self) -> bool:
        return self._capacity is not None and self._size >= self._capacity

    # --- Acceso por índice --------------------------------------------------------------------------

    def node_at(self, index: int) -> RingNode[T]:
        """El nodo del índice index. O(min(i, n - i)).

        Raises:
            TypeError: index no es un entero (bool tampoco cuenta).
            IndexError: index está fuera de [0, size).
        """
        self._check_index(index, self._size)
        return self._walk(index)

    def get(self, index: int) -> T:
        """El valor del índice index. O(min(i, n - i)).

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size).
        """
        return self.node_at(index).value

    def set(self, index: int, value: T) -> None:
        """Escribe value en el índice index. O(min(i, n - i)).

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size).
        """
        self.node_at(index).value = value

    def __getitem__(self, index: int) -> T:
        return self.get(index)

    def __setitem__(self, index: int, value: T) -> None:
        self.set(index, value)

    # --- Inserción ----------------------------------------------------------------------------------

    def append(self, value: T) -> RingNode[T]:
        """Inserta value al final (justo antes de la cabeza) y devuelve su nodo. O(1).

        Raises:
            CapacityError: el anillo ya tiene capacity elementos.
        """
        node = self._new_node(value)
        if self._head is None:
            self._link_alone(node)
        else:
            self._link_before(self._head, node)
        self._size += 1
        return node

    def insert(self, index: int, value: T) -> RingNode[T]:
        """Inserta value para que quede en el índice index y devuelve su nodo. O(min(i, n - i)).

        index puede ir de 0 a size: 0 crea una cabeza nueva y size equivale a append.

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size].
            CapacityError: el anillo ya tiene capacity elementos.
        """
        self._check_index(index, self._size + 1)
        if index == self._size:
            return self.append(value)
        node = self._new_node(value)
        self._link_before(self._walk(index), node)
        if index == 0:
            self._head = node
        self._size += 1
        return node

    def insert_after(self, node: RingNode[T], value: T) -> RingNode[T]:
        """Inserta value justo después de un nodo de ESTE anillo y devuelve el nodo nuevo. O(1).

        Si node es el último, el nuevo queda al final (la cabeza no cambia).

        Raises:
            ValueError: el nodo no pertenece a este anillo.
            CapacityError: el anillo ya tiene capacity elementos.
        """
        self._check_owner(node)
        new_node = self._new_node(value)
        self._link_before(node.next, new_node)
        self._size += 1
        return new_node

    # --- Extracción ---------------------------------------------------------------------------------

    def remove(self, node: RingNode[T]) -> T:
        """Quita un nodo de ESTE anillo y devuelve su valor. O(1).

        Si era la cabeza, la cabeza pasa al siguiente.

        Raises:
            ValueError: el nodo no pertenece a este anillo (ya quitado o de otro anillo).
        """
        self._check_owner(node)
        if self._size == 1:
            self._head = None
        else:
            if node is self._head:
                self._head = node.next
            node.prev.next = node.next
            node.next.prev = node.prev
        node.prev = node.next = None
        node.owner = None
        self._size -= 1
        return node.value

    def remove_at(self, index: int) -> T:
        """Quita el elemento del índice index y devuelve su valor. O(min(i, n - i)).

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size).
        """
        return self.remove(self.node_at(index))

    def clear(self) -> None:
        """Vacía el anillo y desengancha cada nodo (rompe el ciclo). O(n)."""
        node = self._head
        for _ in range(self._size):
            following = node.next
            node.prev = node.next = None
            node.owner = None
            node = following
        self._head = None
        self._size = 0

    # --- Rotación -----------------------------------------------------------------------------------

    def rotate(self, steps: int = 1) -> None:
        """Gira el anillo como collections.deque.rotate: con steps > 0 el último pasa a ser la cabeza.

        Solo mueve el puntero de la cabeza; ningún nodo cambia de enlaces. Con el anillo vacío no hace
        nada. O(min(k, n - k)) con k = steps mod n.

        Raises:
            TypeError: steps no es un entero.
        """
        if isinstance(steps, bool) or not isinstance(steps, int):
            raise TypeError(f"steps debe ser un entero, no {type(steps).__name__}")
        if self._size == 0:
            return
        self._head = self._walk((-steps) % self._size)

    # --- Recorrido ----------------------------------------------------------------------------------

    def __iter__(self) -> Iterator[T]:
        """Una vuelta, de la cabeza al último. O(n)."""
        node = self._head
        for _ in range(self._size):
            yield node.value
            node = node.next

    def __reversed__(self) -> Iterator[T]:
        """Una vuelta al revés, del último a la cabeza. O(n)."""
        if self._head is None:
            return
        node = self._head.prev
        for _ in range(self._size):
            yield node.value
            node = node.prev

    def pairs(self) -> Iterator[tuple[T, T]]:
        """Cada valor con su siguiente, incluido el cierre (último, cabeza): las aristas de un polígono. O(n).

        Con un solo elemento da (x, x); vacío, nada.
        """
        node = self._head
        for _ in range(self._size):
            yield node.value, node.next.value
            node = node.next

    def __repr__(self) -> str:
        values = ", ".join(repr(v) for v in self)
        return f"CircularDoublyLinkedList(head->[{values}], capacity={self._capacity})"

    # --- Enlaces internos ---------------------------------------------------------------------------

    @staticmethod
    def _check_index(index: int, limit: int) -> None:
        """index debe ser un entero en [0, limit). O(1).

        Raises:
            TypeError: index no es un entero (bool tampoco cuenta).
            IndexError: index está fuera de [0, limit).
        """
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError(f"el índice debe ser un entero, no {type(index).__name__}")
        if not 0 <= index < limit:
            raise IndexError(f"índice {index} fuera de rango [0, {limit})")

    def _walk(self, index: int) -> RingNode[T]:
        """El nodo del índice index (ya validado), caminando por el lado más corto. O(min(i, n - i))."""
        node = self._head
        if index <= self._size // 2:
            for _ in range(index):
                node = node.next
        else:
            for _ in range(self._size - index):
                node = node.prev
        return node

    def _new_node(self, value: T) -> RingNode[T]:
        if self.is_full():
            raise CapacityError(f"el anillo está lleno (capacidad {self._capacity})")
        node = RingNode(value)
        node.owner = self
        return node

    def _check_owner(self, node: RingNode[T]) -> None:
        if not isinstance(node, RingNode) or node.owner is not self:
            raise ValueError("el nodo no pertenece a este anillo")

    def _link_alone(self, node: RingNode[T]) -> None:
        """Un anillo de un nodo: se enlaza consigo mismo y es la cabeza."""
        node.prev = node.next = node
        self._head = node

    @staticmethod
    def _link_before(target: RingNode[T], node: RingNode[T]) -> None:
        """Enlaza node justo antes de target. O(1)."""
        node.prev = target.prev
        node.next = target
        target.prev.next = node
        target.prev = node

    # --- Invariantes --------------------------------------------------------------------------------

    def check_invariants(self) -> None:
        """Comprueba la consistencia interna. O(n).

        Raises:
            InvariantError: la cabeza no concuerda con el tamaño, se superó la capacidad, un enlace es
                None, algún prev/next no es recíproco, un nodo no apunta a este anillo, la cabeza aparece
                antes de dar la vuelta (más nodos que size) o no se vuelve a ella tras size pasos.
        """
        if (self._head is None) != (self._size == 0):
            raise InvariantError("cabeza y tamaño no concuerdan")
        if self._size < 0 or (self._capacity is not None and self._size > self._capacity):
            raise InvariantError("tamaño fuera de rango")
        node = self._head
        for step in range(self._size):
            if node.next is None or node.prev is None:
                raise InvariantError("un nodo del anillo tiene un enlace None")
            if node.next.prev is not node:
                raise InvariantError("un next no es recíproco de su prev")
            if node.owner is not self:
                raise InvariantError("un nodo no apunta a este anillo")
            node = node.next
            if node is self._head and step < self._size - 1:
                raise InvariantError(f"el anillo se cierra tras {step + 1} nodos y size es {self._size}")
        if node is not self._head:
            raise InvariantError(f"tras {self._size} pasos no se vuelve a la cabeza")
