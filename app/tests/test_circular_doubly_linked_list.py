"""CircularDoublyLinkedList frente a collections.deque (solo como referencia)."""

import random
from collections import deque

import pytest

from app.structures import CapacityError, CircularDoublyLinkedList, InvariantError, RingNode

OPERATIONS = 5000


def assert_same(ring, reference):
    ring.check_invariants()
    values = list(reference)
    assert len(ring) == len(reference)
    assert bool(ring) == bool(reference)
    assert list(ring) == values
    assert list(reversed(ring)) == values[::-1]
    assert list(ring.pairs()) == list(zip(values, values[1:] + values[:1], strict=True))
    assert (ring.head is None) == (not reference)
    if reference:
        assert ring.head.value == reference[0]
        assert ring.head.prev.value == reference[-1]  # el último enlaza con la cabeza


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_operations_match_a_deque(seed):
    rng = random.Random(seed)
    ring, reference = CircularDoublyLinkedList(), deque()

    for step in range(OPERATIONS):
        action = rng.random()
        size = len(reference)
        index = rng.randint(-2, size + 2)  # incluye negativos y fuera de rango por arriba
        if action < 0.18:
            ring.append(step)
            reference.append(step)
        elif action < 0.36:
            if 0 <= index <= size:
                ring.insert(index, step)
                reference.insert(index, step)
            else:
                with pytest.raises(IndexError):
                    ring.insert(index, step)
        elif action < 0.46:
            if 0 <= index < size:
                ring.insert_after(ring.node_at(index), step)
                reference.insert(index + 1, step)
        elif action < 0.62:
            if 0 <= index < size:
                assert ring.remove_at(index) == reference[index]
                del reference[index]
            else:
                with pytest.raises(IndexError):
                    ring.remove_at(index)
        elif action < 0.70:
            if 0 <= index < size:
                value = reference[index]
                assert ring.remove(ring.node_at(index)) == value
                del reference[index]
        elif action < 0.80:
            if 0 <= index < size:
                assert ring.get(index) == ring[index] == reference[index]
            else:
                with pytest.raises(IndexError):
                    ring.get(index)
        elif action < 0.88:
            if 0 <= index < size:
                ring[index] = -step
                reference[index] = -step
            else:
                with pytest.raises(IndexError):
                    ring.set(index, -step)
        elif action < 0.995:
            steps = rng.randint(-2 * size - 3, 2 * size + 3)
            ring.rotate(steps)
            reference.rotate(steps)
        else:
            ring.clear()
            reference.clear()
        assert_same(ring, reference)


@pytest.mark.parametrize("seed", [10, 11, 12])
def test_random_operations_with_a_capacity(seed):
    rng = random.Random(seed)
    capacity = rng.randint(1, 8)
    ring, reference = CircularDoublyLinkedList(capacity), deque()

    for step in range(OPERATIONS):
        action = rng.random()
        if action < 0.6:
            index = rng.randint(0, len(reference))
            if len(reference) == capacity:
                with pytest.raises(CapacityError):
                    ring.insert(index, step)
                with pytest.raises(CapacityError):
                    ring.append(step)
                with pytest.raises(CapacityError):
                    ring.insert_after(ring.head, step)
            else:
                ring.insert(index, step)
                reference.insert(index, step)
        elif action < 0.9:
            if reference:
                index = rng.randrange(len(reference))
                assert ring.remove_at(index) == reference[index]
                del reference[index]
        else:
            steps = rng.randint(-10, 10)
            ring.rotate(steps)
            reference.rotate(steps)
        assert ring.is_full() == (len(reference) == capacity)
        assert_same(ring, reference)


# --- Casos límite --------------------------------------------------------------------------------


def test_an_empty_ring():
    ring = CircularDoublyLinkedList()

    assert (len(ring), bool(ring), ring.is_empty(), ring.is_full()) == (0, False, True, False)
    assert ring.head is None
    assert (list(ring), list(reversed(ring)), list(ring.pairs())) == ([], [], [])
    for operation in (lambda: ring.get(0), lambda: ring.remove_at(0), lambda: ring.node_at(0)):
        with pytest.raises(IndexError):
            operation()
    ring.rotate(5)  # girar un anillo vacío no hace nada
    ring.check_invariants()


def test_a_single_node_links_to_itself():
    ring = CircularDoublyLinkedList()
    node = ring.append("a")

    assert node.next is node.prev is node is ring.head
    assert list(ring.pairs()) == [("a", "a")]
    ring.rotate(3)
    assert ring.head is node
    assert ring.remove(node) == "a"
    assert ring.head is None
    assert (node.prev, node.next, node.owner) == (None, None, None)
    ring.check_invariants()


def test_the_last_node_closes_the_ring():
    ring = CircularDoublyLinkedList()
    for value in "abc":
        ring.append(value)

    last = ring.node_at(2)
    assert last.next is ring.head  # el siguiente del último es el primero, sin módulo
    assert ring.head.prev is last
    assert list(ring.pairs()) == [("a", "b"), ("b", "c"), ("c", "a")]


def test_removing_index_zero_moves_the_head_to_the_next():
    ring = CircularDoublyLinkedList()
    for value in "abc":
        ring.append(value)

    assert ring.remove_at(0) == "a"
    assert ring.head.value == "b"
    assert list(ring) == ["b", "c"]
    assert ring.head.prev.value == "c"


def test_insert_at_zero_creates_a_new_head_and_at_size_appends():
    ring = CircularDoublyLinkedList()
    ring.insert(0, "b")
    ring.insert(0, "a")
    ring.insert(2, "c")

    assert list(ring) == ["a", "b", "c"]
    assert ring.head.value == "a"


def test_insert_after_the_last_keeps_the_head():
    ring = CircularDoublyLinkedList()
    first = ring.append("a")
    last = ring.append("b")
    ring.insert_after(last, "c")

    assert ring.head is first
    assert list(ring) == ["a", "b", "c"]


def test_rotate_follows_deque_rotate():
    for steps in range(-9, 10):
        ring, reference = CircularDoublyLinkedList(), deque("abcd")
        for value in "abcd":
            ring.append(value)
        ring.rotate(steps)
        reference.rotate(steps)
        assert list(ring) == list(reference)


def test_rotate_only_moves_the_head():
    ring = CircularDoublyLinkedList()
    nodes = [ring.append(value) for value in range(4)]
    links = [(node.prev, node.next) for node in nodes]

    ring.rotate(1)
    assert ring.head is nodes[3]
    assert [(node.prev, node.next) for node in nodes] == links


@pytest.mark.parametrize("steps", [1.0, "1", True, None])
def test_rotate_refuses_a_non_integer(steps):
    ring = CircularDoublyLinkedList()
    ring.append(1)

    with pytest.raises(TypeError):
        ring.rotate(steps)


@pytest.mark.parametrize("index", [-1, -3])
def test_a_negative_index_does_not_count_from_the_end(index):
    ring = CircularDoublyLinkedList()
    for value in range(3):
        ring.append(value)

    for operation in (
        lambda: ring.get(index),
        lambda: ring[index],
        lambda: ring.set(index, 9),
        lambda: ring.node_at(index),
        lambda: ring.remove_at(index),
        lambda: ring.insert(index, 9),
    ):
        with pytest.raises(IndexError):
            operation()
    assert list(ring) == [0, 1, 2]  # los intentos fallidos no cambian nada


@pytest.mark.parametrize("index", [1.0, "0", True, False, None, slice(0, 1)])
def test_a_non_integer_index_is_refused(index):
    ring = CircularDoublyLinkedList()
    for value in range(3):
        ring.append(value)

    for operation in (
        lambda: ring.get(index),
        lambda: ring.__setitem__(index, 9),
        lambda: ring.remove_at(index),
        lambda: ring.insert(index, 9),
    ):
        with pytest.raises(TypeError):
            operation()
    assert list(ring) == [0, 1, 2]


def test_a_node_from_another_ring_or_already_removed_is_refused():
    ring, other = CircularDoublyLinkedList(), CircularDoublyLinkedList()
    ring.append(1)
    foreign = other.append(2)
    removed = ring.append(3)
    ring.remove(removed)

    for node in (foreign, removed, RingNode(4), "not a node"):
        with pytest.raises(ValueError):
            ring.remove(node)
        with pytest.raises(ValueError):
            ring.insert_after(node, 5)
    assert list(ring) == [1]
    assert list(other) == [2]


def test_capacity_one_and_a_full_ring():
    ring = CircularDoublyLinkedList(capacity=1)
    node = ring.append(1)

    assert ring.is_full()
    for operation in (lambda: ring.append(2), lambda: ring.insert(0, 2), lambda: ring.insert_after(node, 2)):
        with pytest.raises(CapacityError):
            operation()
    assert list(ring) == [1]
    assert ring.remove_at(0) == 1
    ring.append(3)
    assert list(ring) == [3]


@pytest.mark.parametrize("capacity", [0, -1, 2.5, "3", True])
def test_an_invalid_capacity_is_refused(capacity):
    with pytest.raises(ValueError):
        CircularDoublyLinkedList(capacity)


def test_clear_unlinks_every_node():
    ring = CircularDoublyLinkedList()
    nodes = [ring.append(value) for value in range(3)]
    ring.clear()

    assert (len(ring), ring.head) == (0, None)
    assert all((node.prev, node.next, node.owner) == (None, None, None) for node in nodes)
    ring.check_invariants()


def test_iterating_does_not_consume_the_ring():
    ring = CircularDoublyLinkedList()
    for value in range(3):
        ring.append(value)

    assert list(ring) == list(ring) == [0, 1, 2]
    assert repr(ring) == "CircularDoublyLinkedList(head->[0, 1, 2], capacity=None)"


def test_check_invariants_detects_a_broken_ring():
    ring = CircularDoublyLinkedList()
    for value in range(4):
        ring.append(value)
    ring.check_invariants()

    ring._size = 3  # se vuelve a la cabeza tras 4 pasos, no tras 3
    with pytest.raises(InvariantError):
        ring.check_invariants()

    ring._size = 5  # la cabeza aparece antes de dar la vuelta
    with pytest.raises(InvariantError):
        ring.check_invariants()

    ring._size = 4
    second = ring.node_at(1)
    second.prev = ring.node_at(3)  # prev no recíproco
    with pytest.raises(InvariantError):
        ring.check_invariants()

    second.prev = ring.head
    second.owner = None  # nodo que no apunta a este anillo
    with pytest.raises(InvariantError):
        ring.check_invariants()

    second.owner = ring
    ring.check_invariants()
    ring._head = None  # cabeza y tamaño no concuerdan
    with pytest.raises(InvariantError):
        ring.check_invariants()


def test_the_internal_storage_is_made_of_nodes():
    ring = CircularDoublyLinkedList()
    ring.append(1)

    assert not any(isinstance(v, (list, deque, dict)) for v in vars(ring).values())
