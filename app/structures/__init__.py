"""Estructuras de datos implementadas a mano (nodos y referencias), sin usar list, deque ni OrderedDict
como contenedor interno. Cada una documenta el coste de sus operaciones, tiene un límite de capacidad
opcional y una función check_invariants() que las pruebas llaman tras cada operación.

La excepción es FixedArray: un arreglo estático que usa una list preasignada de tamaño fijo (se reserva
una sola vez al crearlo y nunca crece ni encoge) y comprueba los límites de cada índice.
"""

from app.structures.doubly_linked_list import DoublyLinkedList, DoublyNode
from app.structures.exceptions import CapacityError, InvariantError
from app.structures.fixed_array import FixedArray
from app.structures.linked_queue import LinkedQueue, QueueNode
from app.structures.linked_stack import LinkedStack, StackNode

__all__ = [
    "CapacityError",
    "DoublyLinkedList",
    "DoublyNode",
    "FixedArray",
    "InvariantError",
    "LinkedQueue",
    "LinkedStack",
    "QueueNode",
    "StackNode",
]
