"""Pila LIFO sobre una lista simplemente enlazada: la cima es la cabeza."""

from collections.abc import Iterator
from typing import Generic, TypeVar

from app.structures.exceptions import CapacityError, InvariantError, validate_capacity

T = TypeVar("T")


class StackNode(Generic[T]):
    """Un eslabón de la pila: el valor y el nodo de debajo."""

    __slots__ = ("below", "value")

    def __init__(self, value: T, below: "StackNode[T] | None" = None):
        self.value = value
        self.below = below


class LinkedStack(Generic[T]):
    """Pila LIFO: se apila y se desapila por la cima.

    Costes (n = elementos):
        push, pop, peek, len, is_empty, is_full: O(1).
        Recorrer (iter, de la cima al fondo), clear: O(n). check_invariants: O(n).
    Memoria: O(n), un nodo por elemento.

    capacity limita los elementos (None = sin límite); push con la pila llena lanza CapacityError.
    """

    def __init__(self, capacity: int | None = None):
        self._capacity = validate_capacity(capacity)
        self._top: StackNode[T] | None = None
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

    def push(self, value: T) -> None:
        """Apila value en la cima. O(1).

        Raises:
            CapacityError: la pila ya tiene capacity elementos.
        """
        if self.is_full():
            raise CapacityError(f"la pila está llena (capacidad {self._capacity})")
        self._top = StackNode(value, self._top)
        self._size += 1

    def pop(self) -> T:
        """Desapila y devuelve la cima (el último apilado). O(1).

        Raises:
            IndexError: la pila está vacía.
        """
        if self._top is None:
            raise IndexError("pop de una pila vacía")
        node = self._top
        self._top = node.below
        node.below = None  # suelta la referencia para no retener la cadena
        self._size -= 1
        return node.value

    def peek(self) -> T:
        """La cima, sin desapilarla. O(1).

        Raises:
            IndexError: la pila está vacía.
        """
        if self._top is None:
            raise IndexError("peek de una pila vacía")
        return self._top.value

    def clear(self) -> None:
        """Vacía la pila. O(n): desengancha cada nodo."""
        node = self._top
        while node is not None:
            node.below, node = None, node.below
        self._top = None
        self._size = 0

    def __iter__(self) -> Iterator[T]:
        """De la cima al fondo, sin modificar la pila. O(n)."""
        node = self._top
        while node is not None:
            yield node.value
            node = node.below

    def __repr__(self) -> str:
        return f"LinkedStack(top->[{', '.join(repr(v) for v in self)}], capacity={self._capacity})"

    def check_invariants(self) -> None:
        """Comprueba la consistencia interna. O(n).

        Raises:
            InvariantError: la cima no concuerda con el tamaño, hay un ciclo, el recorrido no cuenta
                size nodos o se superó la capacidad.
        """
        if (self._top is None) != (self._size == 0):
            raise InvariantError("cima y tamaño no concuerdan")
        if self._size < 0 or (self._capacity is not None and self._size > self._capacity):
            raise InvariantError("tamaño fuera de rango")
        count, node = 0, self._top
        while node is not None:
            count += 1
            if count > self._size:
                raise InvariantError("hay más nodos que size (¿un ciclo?)")
            node = node.below
        if count != self._size:
            raise InvariantError(f"el recorrido cuenta {count} nodos y size es {self._size}")
