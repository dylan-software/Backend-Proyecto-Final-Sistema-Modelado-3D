"""Lista doblemente enlazada con acceso O(1) a sus nodos (la base de la caché LRU)."""

from collections.abc import Iterator
from typing import Generic, TypeVar

from app.structures.exceptions import CapacityError, InvariantError, validate_capacity

T = TypeVar("T")


class DoublyNode(Generic[T]):
    """Un nodo con su valor, el anterior, el siguiente y la lista a la que pertenece (None si a ninguna)."""

    __slots__ = ("next", "owner", "prev", "value")

    def __init__(self, value: T):
        self.value = value
        self.prev: DoublyNode[T] | None = None
        self.next: DoublyNode[T] | None = None
        self.owner: DoublyLinkedList[T] | None = None


class DoublyLinkedList(Generic[T]):
    """Lista doblemente enlazada con punteros a la cabeza (frente) y a la cola (final).

    Las inserciones devuelven el nodo creado: con él, remove y move_to_front son O(1), que es lo que
    necesita una caché LRU (diccionario clave -> nodo + esta lista ordenada por uso).

    Costes (n = elementos):
        push_front, push_back, pop_front, pop_back, peek_front, peek_back, remove(nodo),
        move_to_front(nodo), move_to_back(nodo), len: O(1).
        insert_after(nodo, v), insert_before(nodo, v): O(1) (insertar en cualquier posición con el nodo).
        node_at(i), insert(i, v), remove_at(i): O(min(i, n - i)) (camina desde la cabeza o desde la
            cola, por el lado más corto).
        Recorrer (iter, reversed), clear: O(n). check_invariants: O(n).

    Índices: enteros en [0, size) (insert admite también size, que equivale a push_back). Los negativos
    NO cuentan desde el final (lanzan IndexError) y un índice que no es entero lanza TypeError.
    Memoria: O(n), un nodo por elemento.

    capacity limita los elementos (None = sin límite); insertar con la lista llena lanza CapacityError.
    """

    def __init__(self, capacity: int | None = None):
        self._capacity = validate_capacity(capacity)
        self._head: DoublyNode[T] | None = None
        self._tail: DoublyNode[T] | None = None
        self._size = 0

    @property
    def capacity(self) -> int | None:
        return self._capacity

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def is_empty(self) -> bool:
        return self._size == 0

    def is_full(self) -> bool:
        return self._capacity is not None and self._size >= self._capacity

    @property
    def head(self) -> DoublyNode[T] | None:
        """El nodo del frente (o None). O(1)."""
        return self._head

    @property
    def tail(self) -> DoublyNode[T] | None:
        """El nodo del final (o None). O(1)."""
        return self._tail

    # --- Inserción --------------------------------------------------------------------------------

    def _new_node(self, value: T) -> DoublyNode[T]:
        if self.is_full():
            raise CapacityError(f"la lista está llena (capacidad {self._capacity})")
        node = DoublyNode(value)
        node.owner = self
        return node

    def push_front(self, value: T) -> DoublyNode[T]:
        """Inserta value al frente y devuelve su nodo. O(1).

        Raises:
            CapacityError: la lista ya tiene capacity elementos.
        """
        node = self._new_node(value)
        self._link_front(node)
        self._size += 1
        return node

    def push_back(self, value: T) -> DoublyNode[T]:
        """Inserta value al final y devuelve su nodo. O(1).

        Raises:
            CapacityError: la lista ya tiene capacity elementos.
        """
        node = self._new_node(value)
        self._link_back(node)
        self._size += 1
        return node

    def insert_after(self, node: DoublyNode[T], value: T) -> DoublyNode[T]:
        """Inserta value justo después de un nodo de ESTA lista y devuelve el nodo nuevo. O(1).

        Si node es la cola, el nuevo pasa a ser la cola.

        Raises:
            ValueError: el nodo no pertenece a esta lista.
            CapacityError: la lista ya tiene capacity elementos.
        """
        self._check_owner(node)
        if node is self._tail:
            return self.push_back(value)
        new_node = self._new_node(value)
        self._link_between(node, node.next, new_node)
        self._size += 1
        return new_node

    def insert_before(self, node: DoublyNode[T], value: T) -> DoublyNode[T]:
        """Inserta value justo antes de un nodo de ESTA lista y devuelve el nodo nuevo. O(1).

        Si node es la cabeza, el nuevo pasa a ser la cabeza.

        Raises:
            ValueError: el nodo no pertenece a esta lista.
            CapacityError: la lista ya tiene capacity elementos.
        """
        self._check_owner(node)
        if node is self._head:
            return self.push_front(value)
        new_node = self._new_node(value)
        self._link_between(node.prev, node, new_node)
        self._size += 1
        return new_node

    def insert(self, index: int, value: T) -> DoublyNode[T]:
        """Inserta value para que quede en el índice index y devuelve su nodo. O(min(i, n - i)).

        index puede ir de 0 a size: 0 equivale a push_front y size a push_back.

        Raises:
            TypeError: index no es un entero (bool tampoco cuenta).
            IndexError: index está fuera de [0, size].
            CapacityError: la lista ya tiene capacity elementos.
        """
        self._check_index(index, self._size + 1)
        if index == self._size:
            return self.push_back(value)
        return self.insert_before(self._walk(index), value)

    # --- Acceso por índice --------------------------------------------------------------------------

    def node_at(self, index: int) -> DoublyNode[T]:
        """El nodo del índice index (0 = cabeza). O(min(i, n - i)).

        Raises:
            TypeError: index no es un entero (bool tampoco cuenta).
            IndexError: index está fuera de [0, size).
        """
        self._check_index(index, self._size)
        return self._walk(index)

    # --- Extracción -------------------------------------------------------------------------------

    def pop_front(self) -> T:
        """Quita y devuelve el valor del frente. O(1).

        Raises:
            IndexError: la lista está vacía.
        """
        if self._head is None:
            raise IndexError("pop_front de una lista vacía")
        return self.remove(self._head)

    def pop_back(self) -> T:
        """Quita y devuelve el valor del final (en una LRU, el menos usado). O(1).

        Raises:
            IndexError: la lista está vacía.
        """
        if self._tail is None:
            raise IndexError("pop_back de una lista vacía")
        return self.remove(self._tail)

    def remove(self, node: DoublyNode[T]) -> T:
        """Quita un nodo de ESTA lista y devuelve su valor. O(1).

        Raises:
            ValueError: el nodo no pertenece a esta lista (ya quitado o de otra lista).
        """
        self._check_owner(node)
        self._unlink(node)
        node.owner = None
        self._size -= 1
        return node.value

    def remove_at(self, index: int) -> T:
        """Quita el elemento del índice index y devuelve su valor. O(min(i, n - i)).

        Raises:
            TypeError: index no es un entero (bool tampoco cuenta).
            IndexError: index está fuera de [0, size).
        """
        return self.remove(self.node_at(index))

    def peek_front(self) -> T:
        """El valor del frente, sin quitarlo. O(1).

        Raises:
            IndexError: la lista está vacía.
        """
        if self._head is None:
            raise IndexError("peek_front de una lista vacía")
        return self._head.value

    def peek_back(self) -> T:
        """El valor del final, sin quitarlo. O(1).

        Raises:
            IndexError: la lista está vacía.
        """
        if self._tail is None:
            raise IndexError("peek_back de una lista vacía")
        return self._tail.value

    # --- Reordenar ---------------------------------------------------------------------------------

    def move_to_front(self, node: DoublyNode[T]) -> None:
        """Lleva un nodo de esta lista al frente (en una LRU: "recién usado"). O(1).

        Raises:
            ValueError: el nodo no pertenece a esta lista.
        """
        self._check_owner(node)
        if node is self._head:
            return
        self._unlink(node)
        self._link_front(node)

    def move_to_back(self, node: DoublyNode[T]) -> None:
        """Lleva un nodo de esta lista al final. O(1).

        Raises:
            ValueError: el nodo no pertenece a esta lista.
        """
        self._check_owner(node)
        if node is self._tail:
            return
        self._unlink(node)
        self._link_back(node)

    def clear(self) -> None:
        """Vacía la lista y desengancha cada nodo. O(n)."""
        node = self._head
        while node is not None:
            following = node.next
            node.prev = node.next = None
            node.owner = None
            node = following
        self._head = self._tail = None
        self._size = 0

    # --- Recorrido ---------------------------------------------------------------------------------

    def __iter__(self) -> Iterator[T]:
        """Del frente al final. O(n)."""
        node = self._head
        while node is not None:
            yield node.value
            node = node.next

    def __reversed__(self) -> Iterator[T]:
        """Del final al frente. O(n)."""
        node = self._tail
        while node is not None:
            yield node.value
            node = node.prev

    def __repr__(self) -> str:
        return f"DoublyLinkedList([{', '.join(repr(v) for v in self)}], capacity={self._capacity})"

    # --- Enlaces internos -------------------------------------------------------------------------

    def _check_owner(self, node: DoublyNode[T]) -> None:
        if not isinstance(node, DoublyNode) or node.owner is not self:
            raise ValueError("el nodo no pertenece a esta lista")

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

    def _walk(self, index: int) -> DoublyNode[T]:
        """El nodo del índice index (ya validado), desde la cabeza o la cola según cuál esté más cerca."""
        if index <= self._size // 2:
            node = self._head
            for _ in range(index):
                node = node.next
        else:
            node = self._tail
            for _ in range(self._size - 1 - index):
                node = node.prev
        return node

    @staticmethod
    def _link_between(before: DoublyNode[T], after: DoublyNode[T], node: DoublyNode[T]) -> None:
        """Enlaza node entre dos nodos vecinos que no son None (before.next es after). O(1)."""
        node.prev = before
        node.next = after
        before.next = node
        after.prev = node

    def _link_front(self, node: DoublyNode[T]) -> None:
        node.prev = None
        node.next = self._head
        if self._head is None:
            self._tail = node
        else:
            self._head.prev = node
        self._head = node

    def _link_back(self, node: DoublyNode[T]) -> None:
        node.next = None
        node.prev = self._tail
        if self._tail is None:
            self._head = node
        else:
            self._tail.next = node
        self._tail = node

    def _unlink(self, node: DoublyNode[T]) -> None:
        if node.prev is None:
            self._head = node.next
        else:
            node.prev.next = node.next
        if node.next is None:
            self._tail = node.prev
        else:
            node.next.prev = node.prev
        node.prev = node.next = None

    # --- Invariantes -------------------------------------------------------------------------------

    def check_invariants(self) -> None:
        """Comprueba la consistencia interna. O(n).

        Raises:
            InvariantError: cabeza y cola no concuerdan con el tamaño, algún prev/next no es recíproco,
                un nodo no apunta a esta lista, hay un ciclo, el recorrido no cuenta size nodos o se
                superó la capacidad.
        """
        if (self._head is None) != (self._tail is None) or (self._head is None) != (self._size == 0):
            raise InvariantError("cabeza, cola y tamaño no concuerdan")
        if self._size < 0 or (self._capacity is not None and self._size > self._capacity):
            raise InvariantError("tamaño fuera de rango")
        if self._head is not None and (self._head.prev is not None or self._tail.next is not None):
            raise InvariantError("la cabeza tiene anterior o la cola tiene siguiente")
        count, previous, node = 0, None, self._head
        while node is not None:
            count += 1
            if count > self._size:
                raise InvariantError("hay más nodos que size (¿un ciclo?)")
            if node.prev is not previous:
                raise InvariantError("un prev no apunta al nodo anterior")
            if node.owner is not self:
                raise InvariantError("un nodo no apunta a esta lista")
            previous, node = node, node.next
        if count != self._size:
            raise InvariantError(f"el recorrido cuenta {count} nodos y size es {self._size}")
        if previous is not self._tail:
            raise InvariantError("la cola no es el último nodo")
