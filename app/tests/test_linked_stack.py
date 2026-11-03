"""LinkedStack frente a una list de Python usada como pila (solo como referencia)."""

import random
from collections import deque

import pytest

from app.structures import CapacityError, InvariantError, LinkedStack

OPERATIONS = 5000


def assert_same(stack, reference):
    stack.check_invariants()
    assert len(stack) == len(reference)
    assert list(stack) == reference[::-1]  # de la cima al fondo
    assert bool(stack) == bool(reference)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_operations_match_a_list(seed):
    rng = random.Random(seed)
    stack, reference = LinkedStack(), []

    for step in range(OPERATIONS):
        action = rng.random()
        if action < 0.5:
            stack.push(step)
            reference.append(step)
        elif action < 0.85:
            if reference:
                assert stack.pop() == reference.pop()
            else:
                with pytest.raises(IndexError):
                    stack.pop()
        elif action < 0.97:
            if reference:
                assert stack.peek() == reference[-1]
            else:
                with pytest.raises(IndexError):
                    stack.peek()
        else:
            stack.clear()
            reference.clear()
        assert_same(stack, reference)


@pytest.mark.parametrize("seed", [10, 11, 12])
def test_random_operations_with_a_capacity(seed):
    rng = random.Random(seed)
    capacity = rng.randint(1, 8)
    stack, reference = LinkedStack(capacity), []

    for step in range(OPERATIONS):
        if rng.random() < 0.6:
            if len(reference) == capacity:
                with pytest.raises(CapacityError):
                    stack.push(step)
            else:
                stack.push(step)
                reference.append(step)
        elif reference:
            assert stack.pop() == reference.pop()
        assert stack.is_full() == (len(reference) == capacity)
        assert_same(stack, reference)


# --- Casos límite --------------------------------------------------------------------------------


def test_an_empty_stack():
    stack = LinkedStack()

    assert (len(stack), bool(stack), stack.is_empty(), stack.is_full(), list(stack)) == (
        0,
        False,
        True,
        False,
        [],
    )
    for operation in (stack.pop, stack.peek):
        with pytest.raises(IndexError):
            operation()
    stack.check_invariants()


def test_lifo_order():
    stack = LinkedStack()
    for value in "abc":
        stack.push(value)

    assert stack.peek() == "c"
    assert [stack.pop() for _ in range(3)] == ["c", "b", "a"]
    assert stack.is_empty()


def test_a_single_element():
    stack = LinkedStack()
    stack.push({"vertices": []})

    assert stack.peek() == {"vertices": []}
    assert stack.pop() == {"vertices": []}
    stack.check_invariants()


def test_capacity_one_and_a_full_stack():
    stack = LinkedStack(capacity=1)
    stack.push(1)

    assert stack.is_full()
    with pytest.raises(CapacityError):
        stack.push(2)
    assert list(stack) == [1]  # el intento fallido no cambia nada
    assert stack.pop() == 1
    stack.push(3)
    assert list(stack) == [3]


@pytest.mark.parametrize("capacity", [0, -1, 2.5, "3", True])
def test_an_invalid_capacity_is_refused(capacity):
    with pytest.raises(ValueError):
        LinkedStack(capacity)


def test_iterating_does_not_consume_the_stack():
    stack = LinkedStack()
    for value in range(3):
        stack.push(value)

    assert list(stack) == list(stack) == [2, 1, 0]
    assert repr(stack) == "LinkedStack(top->[2, 1, 0], capacity=None)"


def test_check_invariants_detects_a_wrong_size_and_a_cycle():
    stack = LinkedStack()
    for value in range(3):
        stack.push(value)
    stack._size = 1
    with pytest.raises(InvariantError):
        stack.check_invariants()

    stack._size = 3
    stack._top.below.below.below = stack._top  # ciclo
    with pytest.raises(InvariantError):
        stack.check_invariants()


def test_the_internal_storage_is_made_of_nodes():
    stack = LinkedStack()
    stack.push(1)

    assert not any(isinstance(v, (list, deque, dict)) for v in vars(stack).values())
