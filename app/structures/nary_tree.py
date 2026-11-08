"""Árbol n-ario con raíz: cada nodo tiene cualquier número de hijos ordenados (grupos y sus proyectos)."""

from collections.abc import Iterator
from typing import Generic, TypeVar

from app.structures.exceptions import CapacityError, InvariantError, validate_capacity
from app.structures.linked_stack import LinkedStack

T = TypeVar("T")


class TreeNode(Generic[T]):
    """Un nodo del árbol: su valor, su padre, su primer y su último hijo y sus hermanos vecinos.

    Los hijos de un nodo forman una lista doblemente enlazada (prev_sibling / next_sibling) que va de
    first_child a last_child; así añadir al final y quitar un hijo cuestan O(1) sin usar list.
    owner es el árbol al que pertenece (None si se quitó).
    """

    __slots__ = (
        "child_count",
        "first_child",
        "last_child",
        "next_sibling",
        "owner",
        "parent",
        "prev_sibling",
        "value",
    )

    def __init__(self, value: T):
        self.value = value
        self.parent: TreeNode[T] | None = None
        self.first_child: TreeNode[T] | None = None
        self.last_child: TreeNode[T] | None = None
        self.prev_sibling: TreeNode[T] | None = None
        self.next_sibling: TreeNode[T] | None = None
        self.child_count = 0
        self.owner: NaryTree[T] | None = None

    def is_leaf(self) -> bool:
        return self.first_child is None

    def children(self) -> Iterator["TreeNode[T]"]:
        """Los hijos, del primero al último. O(hijos)."""
        child = self.first_child
        while child is not None:
            yield child
            child = child.next_sibling


class NaryTree(Generic[T]):
    """Árbol n-ario con una raíz fija; los hijos de cada nodo conservan el orden en que se añadieron.

    Costes (n = nodos del árbol, k = nodos del subárbol afectado, h = profundidad del nodo):
        root, len, add_child, children de un nodo (empezar), child_count, is_leaf: O(1).
        remove(nodo): O(k) (desengancha el subárbol en O(1) y suelta sus k nodos).
        depth(nodo): O(h).
        depth_first / walk (preorden con pila propia LinkedStack), count(nodo): O(k) tiempo y O(k)
            memoria de la pila en el peor caso. values, check_invariants: O(n).
    Memoria: O(n), un nodo por valor; cada nodo guarda 6 referencias y un contador.

    capacity limita el total de nodos incluida la raíz (None = sin límite); add_child con el árbol lleno
    lanza CapacityError. La raíz no se puede quitar.
    """

    def __init__(self, root_value: T, capacity: int | None = None):
        self._capacity = validate_capacity(capacity)
        self._root: TreeNode[T] = self._new_node(root_value, check=False)
        self._size = 1

    @property
    def capacity(self) -> int | None:
        return self._capacity

    @property
    def root(self) -> TreeNode[T]:
        """La raíz (siempre existe). O(1)."""
        return self._root

    def __len__(self) -> int:
        return self._size

    def is_full(self) -> bool:
        return self._capacity is not None and self._size >= self._capacity

    # --- Modificación -------------------------------------------------------------------------------

    def add_child(self, parent: TreeNode[T], value: T) -> TreeNode[T]:
        """Añade value como ÚLTIMO hijo de parent (un nodo de este árbol) y devuelve su nodo. O(1).

        Raises:
            ValueError: parent no pertenece a este árbol.
            CapacityError: el árbol ya tiene capacity nodos.
        """
        self._check_owner(parent)
        node = self._new_node(value)
        node.parent = parent
        if parent.last_child is None:
            parent.first_child = node
        else:
            node.prev_sibling = parent.last_child
            parent.last_child.next_sibling = node
        parent.last_child = node
        parent.child_count += 1
        self._size += 1
        return node

    def remove(self, node: TreeNode[T]) -> int:
        """Quita node y todo su subárbol; devuelve cuántos nodos salieron. O(k).

        Raises:
            ValueError: node no pertenece a este árbol o es la raíz.
        """
        self._check_owner(node)
        if node is self._root:
            raise ValueError("la raíz no se puede quitar")
        parent = node.parent
        # Desenganche O(1) de la lista de hermanos.
        if node.prev_sibling is None:
            parent.first_child = node.next_sibling
        else:
            node.prev_sibling.next_sibling = node.next_sibling
        if node.next_sibling is None:
            parent.last_child = node.prev_sibling
        else:
            node.next_sibling.prev_sibling = node.prev_sibling
        parent.child_count -= 1
        node.parent = node.prev_sibling = node.next_sibling = None
        # Suelta los k nodos del subárbol para que no se puedan usar con este árbol.
        removed = 0
        # El recorrido usa solo los enlaces de hijos, así que cambiar owner mientras avanza no lo afecta.
        for inner in self._preorder(node):
            inner.owner = None
            removed += 1
        self._size -= removed
        return removed

    # --- Consultas y recorridos ---------------------------------------------------------------------

    def depth(self, node: TreeNode[T]) -> int:
        """Aristas desde la raíz hasta node (la raíz tiene 0). O(h).

        Raises:
            ValueError: node no pertenece a este árbol.
        """
        self._check_owner(node)
        levels = 0
        while node.parent is not None:
            node = node.parent
            levels += 1
        return levels

    def walk(self, start: TreeNode[T] | None = None) -> Iterator[tuple[TreeNode[T], int]]:
        """Recorrido en profundidad en PREORDEN desde start (la raíz si es None): (nodo, profundidad).

        La profundidad es relativa a start (start tiene 0). Usa una LinkedStack en vez de recursión, así
        que un árbol muy profundo no agota la pila de Python. No modifiques el árbol mientras recorres.
        O(k).

        Raises:
            ValueError: start no pertenece a este árbol.
        """
        if start is None:
            start = self._root
        self._check_owner(start)
        stack: LinkedStack[tuple[TreeNode[T], int]] = LinkedStack()
        stack.push((start, 0))
        while stack:
            node, level = stack.pop()
            yield node, level
            # Se apilan del último al primero para que el primer hijo salga antes (orden de izquierda a
            # derecha).
            child = node.last_child
            while child is not None:
                stack.push((child, level + 1))
                child = child.prev_sibling

    def depth_first(self, start: TreeNode[T] | None = None) -> Iterator[TreeNode[T]]:
        """Los nodos del subárbol de start en preorden (padre antes que sus hijos). O(k).

        Raises:
            ValueError: start no pertenece a este árbol.
        """
        for node, _ in self.walk(start):
            yield node

    def count(self, start: TreeNode[T] | None = None) -> int:
        """Cuántos nodos tiene el subárbol de start, contando start, con un recorrido en profundidad. O(k).

        Raises:
            ValueError: start no pertenece a este árbol.
        """
        total = 0
        for _ in self.walk(start):
            total += 1
        return total

    def values(self) -> Iterator[T]:
        """Los valores de todo el árbol en preorden. O(n)."""
        for node in self.depth_first():
            yield node.value

    def __iter__(self) -> Iterator[T]:
        return self.values()

    def __repr__(self) -> str:
        return f"NaryTree(size={self._size}, capacity={self._capacity}, root={self._root.value!r})"

    # --- Internos -----------------------------------------------------------------------------------

    def _new_node(self, value: T, check: bool = True) -> TreeNode[T]:
        if check and self.is_full():
            raise CapacityError(f"el árbol está lleno (capacidad {self._capacity})")
        node = TreeNode(value)
        node.owner = self
        return node

    def _check_owner(self, node: TreeNode[T]) -> None:
        if not isinstance(node, TreeNode) or node.owner is not self:
            raise ValueError("el nodo no pertenece a este árbol")

    @staticmethod
    def _preorder(start: TreeNode[T]) -> Iterator[TreeNode[T]]:
        """Preorden sin comprobar el dueño (para soltar un subárbol ya desenganchado). O(k)."""
        stack: LinkedStack[TreeNode[T]] = LinkedStack()
        stack.push(start)
        while stack:
            node = stack.pop()
            yield node
            child = node.last_child
            while child is not None:
                stack.push(child)
                child = child.prev_sibling

    # --- Invariantes --------------------------------------------------------------------------------

    def check_invariants(self) -> None:
        """Comprueba la consistencia interna. O(n).

        Raises:
            InvariantError: la raíz tiene padre o hermanos, un nodo no apunta a este árbol, los enlaces
                de hermanos no son recíprocos, first/last_child o child_count no concuerdan con la lista
                de hijos, un hijo no apunta a su padre, hay un ciclo o el recorrido no cuenta size nodos,
                o se superó la capacidad.
        """
        root = self._root
        if root.parent is not None or root.prev_sibling is not None or root.next_sibling is not None:
            raise InvariantError("la raíz tiene padre o hermanos")
        if self._capacity is not None and self._size > self._capacity:
            raise InvariantError("tamaño por encima de la capacidad")
        seen = 0
        stack: LinkedStack[TreeNode[T]] = LinkedStack()
        stack.push(root)
        while stack:
            node = stack.pop()
            seen += 1
            if seen > self._size:
                raise InvariantError("hay más nodos que size (¿un ciclo?)")
            if node.owner is not self:
                raise InvariantError("un nodo no apunta a este árbol")
            children, previous, child = 0, None, node.first_child
            while child is not None:
                children += 1
                if children > self._size:
                    raise InvariantError("la lista de hijos no termina (¿un ciclo?)")
                if child.parent is not node:
                    raise InvariantError("un hijo no apunta a su padre")
                if child.prev_sibling is not previous:
                    raise InvariantError("prev_sibling no es recíproco de next_sibling")
                stack.push(child)
                previous, child = child, child.next_sibling
            if node.last_child is not previous:
                raise InvariantError("last_child no es el último hijo")
            if node.child_count != children:
                raise InvariantError(f"child_count es {node.child_count} y hay {children} hijos")
        if seen != self._size:
            raise InvariantError(f"el recorrido cuenta {seen} nodos y size es {self._size}")
