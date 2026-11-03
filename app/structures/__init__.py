"""Estructuras de datos implementadas a mano (nodos y referencias), sin usar list, deque ni OrderedDict
como contenedor interno. Cada una documenta el coste de sus operaciones, tiene un límite de capacidad
opcional y una función check_invariants() que las pruebas llaman tras cada operación.
"""

from app.structures.exceptions import CapacityError, InvariantError
from app.structures.linked_queue import LinkedQueue, QueueNode

__all__ = ["CapacityError", "InvariantError", "LinkedQueue", "QueueNode"]
