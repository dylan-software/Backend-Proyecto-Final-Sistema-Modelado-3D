"""Cola FIFO sobre una lista simplemente enlazada con punteros a la cabeza y a la cola."""

from collections.abc import Iterator
from typing import Generic, TypeVar

from app.structures.exceptions import CapacityError, InvariantError, validate_capacity

T = TypeVar("T")


class QueueNode(Generic[T]):
    """Un eslabón de la cola: el valor y el siguiente nodo (hacia el final)."""

    __slots__ = ("next", "value")

    def __init__(self, value: T):
        self.value = value
        self.next: QueueNode[T] | None = None


class LinkedQueue(Generic[T]):
    """Cola FIFO: se añade por el final y se saca por el frente.

    Costes (n = elementos):
        enqueue, dequeue, peek, peek_back, len, is_empty, is_full: O(1).
        Recorrer (iter), clear: O(n). check_invariants: O(n).
    Memoria: O(n), un nodo por elemento.

    capacity limita los elementos (None = sin límite); enqueue con la cola llena lanza CapacityError.
    """

    def __init__(self, capacity: int | None = None):
        self._capacity = validate_capacity(capacity)
        self._head: QueueNode[T] | None = None  # frente: el siguiente en salir
        self._tail: QueueNode[T] | None = None  # final: el último en entrar
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

    def enqueue(self, value: T) -> None:
        """Añade value al final. O(1).

        Raises:
            CapacityError: la cola ya tiene capacity elementos.
        """
        if self.is_full():
            raise CapacityError(f"la cola está llena (capacidad {self._capacity})")
        node = QueueNode(value)
        if self._tail is None:
            self._head = node
        else:
            self._tail.next = node
        self._tail = node
        self._size += 1

    def dequeue(self) -> T:
        """Saca y devuelve el elemento del frente (el más antiguo). O(1).

        Raises:
            IndexError: la cola está vacía.
        """
        if self._head is None:
            raise IndexError("dequeue de una cola vacía")
        node = self._head
        self._head = node.next
        if self._head is None:
            self._tail = None
        node.next = None  # suelta la referencia para no retener la cadena
        self._size -= 1
        return node.value

    def peek(self) -> T:
        """El elemento del frente, sin sacarlo. O(1).

        Raises:
            IndexError: la cola está vacía.
        """
        if self._head is None:
            raise IndexError("peek de una cola vacía")
        return self._head.value

    def peek_back(self) -> T:
        """El último elemento añadido, sin sacarlo. O(1).

        Raises:
            IndexError: la cola está vacía.
        """
        if self._tail is None:
            raise IndexError("peek_back de una cola vacía")
        return self._tail.value

    def clear(self) -> None:
        """Vacía la cola. O(n): desengancha cada nodo."""
        node = self._head
        while node is not None:
            node.next, node = None, node.next
        self._head = self._tail = None
        self._size = 0

    def __iter__(self) -> Iterator[T]:
        """Del frente al final, sin modificar la cola. O(n)."""
        node = self._head
        while node is not None:
            yield node.value
            node = node.next

    def __repr__(self) -> str:
        return f"LinkedQueue([{', '.join(repr(v) for v in self)}], capacity={self._capacity})"

    def check_invariants(self) -> None:
        """Comprueba la consistencia interna. O(n).

        Raises:
            InvariantError: cabeza y cola no concuerdan con el tamaño, la cola no es el último nodo,
                hay un ciclo, el recorrido no cuenta size nodos o se superó la capacidad.
        """
        if (self._head is None) != (self._tail is None) or (self._head is None) != (self._size == 0):
            raise InvariantError("cabeza, cola y tamaño no concuerdan")
        if self._size < 0 or (self._capacity is not None and self._size > self._capacity):
            raise InvariantError("tamaño fuera de rango")
        count, node, last = 0, self._head, None
        while node is not None:
            count += 1
            if count > self._size:
                raise InvariantError("hay más nodos que size (¿un ciclo?)")
            last, node = node, node.next
        if count != self._size:
            raise InvariantError(f"el recorrido cuenta {count} nodos y size es {self._size}")
        if last is not self._tail:
            raise InvariantError("la cola no apunta al último nodo")
