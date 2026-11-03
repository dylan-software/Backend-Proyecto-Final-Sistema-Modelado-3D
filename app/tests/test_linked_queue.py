"""LinkedQueue frente a collections.deque (solo como referencia), con miles de operaciones aleatorias."""

import random
from collections import deque

import pytest

from app.structures import CapacityError, InvariantError, LinkedQueue

OPERATIONS = 5000


def assert_same(queue, reference):
    queue.check_invariants()
    assert len(queue) == len(reference)
    assert list(queue) == list(reference)
    assert bool(queue) == bool(reference)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_operations_match_deque(seed):
    rng = random.Random(seed)
    queue, reference = LinkedQueue(), deque()

    for step in range(OPERATIONS):
        action = rng.random()
        if action < 0.5:
            queue.enqueue(step)
            reference.append(step)
        elif action < 0.85:
            if reference:
                assert queue.dequeue() == reference.popleft()
            else:
                with pytest.raises(IndexError):
                    queue.dequeue()
        elif action < 0.95:
            if reference:
                assert (queue.peek(), queue.peek_back()) == (reference[0], reference[-1])
            else:
                with pytest.raises(IndexError):
                    queue.peek()
        else:
            queue.clear()
            reference.clear()
        assert_same(queue, reference)


@pytest.mark.parametrize("seed", [10, 11, 12])
def test_random_operations_with_a_capacity_match_a_bounded_reference(seed):
    rng = random.Random(seed)
    capacity = rng.randint(1, 8)
    queue, reference = LinkedQueue(capacity), deque()

    for step in range(OPERATIONS):
        if rng.random() < 0.6:
            if len(reference) == capacity:
                with pytest.raises(CapacityError):
                    queue.enqueue(step)
            else:
                queue.enqueue(step)
                reference.append(step)
        elif reference:
            assert queue.dequeue() == reference.popleft()
        assert queue.is_full() == (len(reference) == capacity)
        assert_same(queue, reference)


# --- Casos límite --------------------------------------------------------------------------------


def test_an_empty_queue():
    queue = LinkedQueue()

    assert (len(queue), bool(queue), queue.is_empty(), queue.is_full(), list(queue)) == (
        0,
        False,
        True,
        False,
        [],
    )
    for operation in (queue.dequeue, queue.peek, queue.peek_back):
        with pytest.raises(IndexError):
            operation()
    queue.check_invariants()


def test_a_single_element_is_front_and_back():
    queue = LinkedQueue()
    queue.enqueue("a")

    assert (queue.peek(), queue.peek_back()) == ("a", "a")
    assert queue.dequeue() == "a"
    assert queue.is_empty()
    queue.check_invariants()


def test_fifo_order_survives_emptying_and_refilling():
    queue = LinkedQueue()
    for value in "abc":
        queue.enqueue(value)
    assert [queue.dequeue() for _ in range(3)] == ["a", "b", "c"]
    queue.enqueue("d")

    assert (queue.peek(), queue.peek_back(), list(queue)) == ("d", "d", ["d"])
    queue.check_invariants()


def test_capacity_one_holds_exactly_one():
    queue = LinkedQueue(capacity=1)
    queue.enqueue(1)

    assert queue.is_full()
    with pytest.raises(CapacityError):
        queue.enqueue(2)
    assert list(queue) == [1]  # el intento fallido no cambia nada
    queue.dequeue()
    queue.enqueue(3)
    assert list(queue) == [3]


def test_a_full_queue_accepts_again_after_a_dequeue():
    queue = LinkedQueue(capacity=3)
    for value in range(3):
        queue.enqueue(value)

    with pytest.raises(CapacityError):
        queue.enqueue(99)
    assert queue.dequeue() == 0
    queue.enqueue(3)
    assert list(queue) == [1, 2, 3]
    queue.check_invariants()


@pytest.mark.parametrize("capacity", [0, -1, 2.5, "3", True])
def test_an_invalid_capacity_is_refused(capacity):
    with pytest.raises(ValueError):
        LinkedQueue(capacity)


def test_none_values_are_stored_like_any_other():
    queue = LinkedQueue()
    queue.enqueue(None)

    assert len(queue) == 1 and queue.peek() is None and queue.dequeue() is None


def test_iterating_does_not_consume_the_queue():
    queue = LinkedQueue()
    for value in range(5):
        queue.enqueue(value)

    assert list(queue) == list(queue) == [0, 1, 2, 3, 4]
    assert repr(queue) == "LinkedQueue([0, 1, 2, 3, 4], capacity=None)"


def test_check_invariants_detects_a_corrupted_queue():
    queue = LinkedQueue()
    for value in range(3):
        queue.enqueue(value)
    queue._size = 5

    with pytest.raises(InvariantError):
        queue.check_invariants()


def test_check_invariants_detects_a_cycle():
    queue = LinkedQueue()
    for value in range(3):
        queue.enqueue(value)
    queue._tail.next = queue._head  # ciclo

    with pytest.raises(InvariantError):
        queue.check_invariants()


def test_the_internal_storage_is_made_of_nodes():
    queue = LinkedQueue()
    queue.enqueue(1)

    # Ningún contenedor de la librería estándar: solo nodos enlazados.
    assert not any(isinstance(v, (list, deque, dict)) for v in vars(queue).values())
