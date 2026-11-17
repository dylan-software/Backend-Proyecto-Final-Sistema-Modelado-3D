"""Arreglo estático: size casillas reservadas una sola vez, con comprobación de límites."""

from collections.abc import Iterator
from typing import Generic, TypeVar

from app.structures.exceptions import CapacityError, InvariantError, validate_capacity

T = TypeVar("T")


class FixedArray(Generic[T]):
    """Arreglo de tamaño fijo sobre una list preasignada.

    La reserva se hace una sola vez en el constructor ([default] * size) y nunca crece ni encoge:
    no hay append, insert ni pop. Se leen y escriben casillas existentes, y insert_at / remove_at
    desplazan elementos DENTRO de la misma reserva, como en el arreglo estático del curso.

    Costes (n = size, i = índice):
        get, set, a[i], a[i] = v, len, size: O(1).
        insert_at(i, v), remove_at(i): O(n - i) (desplazan las casillas de i en adelante).
        fill, index_of, recorrer (iter): O(n). check_invariants: O(1).
    Memoria: O(n), reservada al crear el arreglo.

    Casilla libre: la que es igual (==) a default, la misma comparación que usa index_of. insert_at
    necesita libre la ÚLTIMA casilla, porque el desplazamiento la sobrescribe; si está ocupada lanza
    CapacityError y no cambia nada. remove_at rellena la última casilla con default.

    Índices: solo enteros en [0, size). Los negativos NO cuentan desde el final (lanzan IndexError) y
    no se admiten slices. default se comparte (es el mismo objeto) en todas las casillas iniciales.
    """

    __slots__ = ("_default", "_size", "_slots")

    def __init__(self, size: int, default: T | None = None):
        """Reserva size casillas con default. O(n).

        Raises:
            ValueError: size no es un entero mayor que 0 (None y bool tampoco valen).
        """
        try:
            valid_size = validate_capacity(size)
        except ValueError:
            valid_size = None
        if valid_size is None:
            raise ValueError("size debe ser un entero mayor que 0")
        self._size: int = valid_size
        self._default: T | None = default  # lo usa remove_at para rellenar la casilla que queda libre
        self._slots: list[T | None] = [default] * valid_size

    @property
    def size(self) -> int:
        return self._size

    def __len__(self) -> int:
        return self._size

    def _check_index(self, index: int) -> None:
        """Comprueba el tipo y el rango del índice. O(1).

        Raises:
            TypeError: index no es un entero (bool tampoco cuenta).
            IndexError: index está fuera de [0, size).
        """
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError(f"el índice debe ser un entero, no {type(index).__name__}")
        if not 0 <= index < self._size:
            raise IndexError(f"índice {index} fuera de rango [0, {self._size})")

    def get(self, index: int) -> T | None:
        """El valor de la casilla index. O(1).

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size).
        """
        self._check_index(index)
        return self._slots[index]

    def set(self, index: int, value: T) -> None:
        """Escribe value en la casilla index. O(1).

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size).
        """
        self._check_index(index)
        self._slots[index] = value

    def __getitem__(self, index: int) -> T | None:
        return self.get(index)

    def __setitem__(self, index: int, value: T) -> None:
        self.set(index, value)

    def insert_at(self, index: int, value: T) -> None:
        """Escribe value en index desplazando una casilla a la derecha las de index en adelante. O(n - i).

        La última casilla desaparece con el desplazamiento, así que tiene que estar libre (== default).

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size).
            CapacityError: la última casilla está ocupada (no se pierde nada: el arreglo no cambia).
        """
        self._check_index(index)
        last = self._size - 1
        if self._slots[last] != self._default:
            raise CapacityError(f"la última casilla ({last}) está ocupada: no hay sitio para desplazar")
        for position in range(last, index, -1):
            self._slots[position] = self._slots[position - 1]
        self._slots[index] = value

    def remove_at(self, index: int) -> T | None:
        """Quita y devuelve la casilla index desplazando a la izquierda las siguientes. O(n - i).

        La última casilla queda libre: se rellena con default.

        Raises:
            TypeError: index no es un entero.
            IndexError: index está fuera de [0, size).
        """
        self._check_index(index)
        removed = self._slots[index]
        last = self._size - 1
        for position in range(index, last):
            self._slots[position] = self._slots[position + 1]
        self._slots[last] = self._default
        return removed

    def fill(self, value: T) -> None:
        """Escribe value en todas las casillas, sin cambiar la reserva. O(n)."""
        for index in range(self._size):
            self._slots[index] = value

    def index_of(self, value: T) -> int:
        """El primer índice cuya casilla es igual (==) a value, o -1 si no está. O(n)."""
        for index in range(self._size):
            if self._slots[index] == value:
                return index
        return -1

    def __iter__(self) -> Iterator[T | None]:
        """Las casillas de 0 a size - 1, sin modificar el arreglo. O(n)."""
        for index in range(self._size):
            yield self._slots[index]

    def __repr__(self) -> str:
        return f"FixedArray([{', '.join(repr(v) for v in self)}], size={self._size})"

    def check_invariants(self) -> None:
        """Comprueba que la reserva sigue siendo una list de exactamente size casillas. O(1).

        Raises:
            InvariantError: size no es un entero positivo o la reserva cambió de largo.
        """
        if isinstance(self._size, bool) or not isinstance(self._size, int) or self._size <= 0:
            raise InvariantError("size no es un entero mayor que 0")
        if not isinstance(self._slots, list):
            raise InvariantError("la reserva no es una list")
        if len(self._slots) != self._size:
            raise InvariantError(f"la reserva tiene {len(self._slots)} casillas y size es {self._size}")
