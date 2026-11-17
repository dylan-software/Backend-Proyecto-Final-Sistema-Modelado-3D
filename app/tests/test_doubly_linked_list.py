"""DoublyLinkedList frente a una list de Python (solo como referencia), con miles de operaciones aleatorias.

La referencia guarda pares (id, valor) y un diccionario id -> nodo, como hará la caché LRU, para
poder probar remove y move_to_front sobre nodos arbitrarios.
"""

import random
from collections import deque

import pytest

from app.structures import CapacityError, DoublyLinkedList, DoublyNode, InvariantError

OPERATIONS = 5000


def assert_same(linked, reference):
    linked.check_invariants()
    values = [value for _, value in reference]
    assert len(linked) == len(reference)
    assert list(linked) == values
    assert list(reversed(linked)) == values[::-1]


def run_random(rng, capacity=None):
    linked, reference, nodes = DoublyLinkedList(capacity), [], {}
    for step in range(OPERATIONS):
        action = rng.random()
        full = capacity is not None and len(reference) == capacity
        if action < 0.45:
            # Insertar: al frente (< 0.25) o al final.
            push = linked.push_front if action < 0.25 else linked.push_back
            if full:
                with pytest.raises(CapacityError):
                    push(step)
            else:
                nodes[step] = push(step)
                reference.insert(0 if action < 0.25 else len(reference), (step, step))
        elif action < 0.55:
            if reference:
                key, value = reference.pop(0)
                assert linked.pop_front() == value
                del nodes[key]
            else:
                with pytest.raises(IndexError):
                    linked.pop_front()
        elif action < 0.65:
            if reference:
                key, value = reference.pop()
                assert linked.pop_back() == value
                del nodes[key]
            else:
                with pytest.raises(IndexError):
                    linked.pop_back()
        elif action < 0.8 and reference:
            index = rng.randrange(len(reference))
            key, value = reference.pop(index)
            assert linked.remove(nodes.pop(key)) == value
        elif action < 0.93 and reference:
            index = rng.randrange(len(reference))
            entry = reference.pop(index)
            linked.move_to_front(nodes[entry[0]])
            reference.insert(0, entry)
        elif action < 0.98 and reference:
            index = rng.randrange(len(reference))
            entry = reference.pop(index)
            linked.move_to_back(nodes[entry[0]])
            reference.append(entry)
        elif action >= 0.98:
            linked.clear()
            reference.clear()
            nodes.clear()
        assert_same(linked, reference)
        if reference:
            assert (linked.peek_front(), linked.peek_back()) == (reference[0][1], reference[-1][1])


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_operations_match_a_list(seed):
    run_random(random.Random(seed))


@pytest.mark.parametrize("seed", [10, 11, 12])
def test_random_operations_with_a_capacity(seed):
    rng = random.Random(seed)
    run_random(rng, capacity=rng.randint(1, 8))


@pytest.mark.parametrize("seed", [20, 21])
def test_push_and_pop_at_both_ends_match_deque(seed):
    rng = random.Random(seed)
    linked, reference = DoublyLinkedList(), deque()
    for step in range(OPERATIONS):
        action = rng.randrange(4)
        if action == 0:
            linked.push_front(step)
            reference.appendleft(step)
        elif action == 1:
            linked.push_back(step)
            reference.append(step)
        elif action == 2 and reference:
            assert linked.pop_front() == reference.popleft()
        elif action == 3 and reference:
            assert linked.pop_back() == reference.pop()
        linked.check_invariants()
        assert list(linked) == list(reference)


def run_random_positions(rng, capacity=None):
    """Inserta y quita en cualquier posición (por índice y por nodo) frente a una list."""
    linked, reference = DoublyLinkedList(capacity), []
    for step in range(OPERATIONS):
        action = rng.random()
        full = capacity is not None and len(reference) == capacity
        if action < 0.3:
            index = rng.randint(0, len(reference))
            if full:
                with pytest.raises(CapacityError):
                    linked.insert(index, step)
            else:
                assert linked.insert(index, step).value == step
                reference.insert(index, step)
        elif action < 0.45 and reference:
            index = rng.randrange(len(reference))
            if full:
                with pytest.raises(CapacityError):
                    linked.insert_after(linked.node_at(index), step)
            else:
                assert linked.insert_after(linked.node_at(index), step).value == step
                reference.insert(index + 1, step)
        elif action < 0.6 and reference:
            index = rng.randrange(len(reference))
            if full:
                with pytest.raises(CapacityError):
                    linked.insert_before(linked.node_at(index), step)
            else:
                assert linked.insert_before(linked.node_at(index), step).value == step
                reference.insert(index, step)
        elif action < 0.9:
            if reference:
                index = rng.randrange(len(reference))
                assert linked.remove_at(index) == reference.pop(index)
            else:
                with pytest.raises(IndexError):
                    linked.remove_at(0)
        elif reference:
            index = rng.randrange(len(reference))
            assert linked.node_at(index).value == reference[index]
        assert_same(linked, [(v, v) for v in reference])
        if reference:
            assert (linked.head.value, linked.tail.value) == (reference[0], reference[-1])


@pytest.mark.parametrize("seed", [30, 31, 32, 33])
def test_inserting_and_removing_at_any_position_match_a_list(seed):
    run_random_positions(random.Random(seed))


@pytest.mark.parametrize("seed", [40, 41])
def test_inserting_at_any_position_with_a_capacity(seed):
    rng = random.Random(seed)
    run_random_positions(rng, capacity=rng.randint(1, 8))


def test_insert_at_the_ends_and_in_the_middle():
    linked = DoublyLinkedList()
    linked.insert(0, "b")  # lista vacía: el 0 es el final
    linked.insert(0, "a")  # nueva cabeza
    linked.insert(2, "d")  # índice size: nueva cola
    c = linked.insert(2, "c")

    assert list(linked) == ["a", "b", "c", "d"]
    assert (linked.head.value, linked.tail.value) == ("a", "d")
    assert (c.prev.value, c.next.value) == ("b", "d")
    assert [linked.node_at(i).value for i in range(4)] == ["a", "b", "c", "d"]
    linked.check_invariants()


def test_insert_after_and_before_update_head_and_tail():
    linked = DoublyLinkedList()
    middle = linked.push_back("m")
    last = linked.insert_after(middle, "z")
    first = linked.insert_before(middle, "a")

    assert (linked.head, linked.tail) == (first, last)
    linked.insert_after(first, "b")
    linked.insert_before(last, "y")
    assert list(linked) == ["a", "b", "m", "y", "z"]
    assert list(reversed(linked)) == ["z", "y", "m", "b", "a"]
    linked.check_invariants()


def test_remove_at_the_head_the_middle_and_the_tail():
    linked = DoublyLinkedList()
    for value in "abcde":
        linked.push_back(value)

    assert linked.remove_at(0) == "a"
    assert linked.remove_at(3) == "e"
    assert linked.remove_at(1) == "c"
    assert list(linked) == ["b", "d"]
    assert (linked.head.value, linked.tail.value) == ("b", "d")
    linked.check_invariants()


@pytest.mark.parametrize("index", [-1, 3, 10])
def test_an_out_of_range_index_is_refused(index):
    linked = DoublyLinkedList()
    for value in range(3):
        linked.push_back(value)

    for operation in (lambda: linked.node_at(index), lambda: linked.remove_at(index)):
        with pytest.raises(IndexError):
            operation()
    if index != 3:  # 3 = size es válido para insert
        with pytest.raises(IndexError):
            linked.insert(index, 9)
    assert list(linked) == [0, 1, 2]


@pytest.mark.parametrize("index", [1.0, "1", True, None])
def test_an_index_that_is_not_an_integer_is_refused(index):
    linked = DoublyLinkedList()
    linked.push_back(1)

    for operation in (
        lambda: linked.node_at(index),
        lambda: linked.remove_at(index),
        lambda: linked.insert(index, 9),
    ):
        with pytest.raises(TypeError):
            operation()
    assert list(linked) == [1]


def test_insert_next_to_a_foreign_or_removed_node_is_refused():
    one, other = DoublyLinkedList(), DoublyLinkedList()
    gone = one.push_back(1)
    one.remove(gone)
    foreign = other.push_back(2)

    for wrong in (gone, foreign, DoublyNode(3), "not a node"):
        for operation in (one.insert_after, one.insert_before):
            with pytest.raises(ValueError):
                operation(wrong, 9)
    assert (list(one), list(other)) == ([], [2])


def test_a_full_list_refuses_every_kind_of_insert():
    linked = DoublyLinkedList(capacity=2)
    first = linked.push_back(1)
    linked.push_back(2)

    for operation in (
        lambda: linked.insert(1, 9),
        lambda: linked.insert_after(first, 9),
        lambda: linked.insert_before(first, 9),
    ):
        with pytest.raises(CapacityError):
            operation()
    assert list(linked) == [1, 2]
    linked.check_invariants()


# --- Uso como LRU ---------------------------------------------------------------------------------


def test_lru_pattern_move_to_front_and_evict_from_the_back():
    linked = DoublyLinkedList()
    nodes = {key: linked.push_front(key) for key in "abc"}  # orden: c, b, a

    linked.move_to_front(nodes["a"])  # "a" recién usado
    assert list(linked) == ["a", "c", "b"]
    assert linked.pop_back() == "b"  # el menos usado sale
    linked.check_invariants()


# --- Casos límite --------------------------------------------------------------------------------


def test_an_empty_list():
    linked = DoublyLinkedList()

    assert (len(linked), bool(linked), linked.head, linked.tail, list(linked)) == (0, False, None, None, [])
    for operation in (linked.pop_front, linked.pop_back, linked.peek_front, linked.peek_back):
        with pytest.raises(IndexError):
            operation()
    linked.check_invariants()


def test_a_single_node_is_head_and_tail_and_can_move_or_go():
    linked = DoublyLinkedList()
    node = linked.push_back("x")

    assert linked.head is node is linked.tail
    linked.move_to_front(node)
    linked.move_to_back(node)
    assert linked.remove(node) == "x"
    assert linked.is_empty()
    linked.check_invariants()


def test_removing_head_middle_and_tail_keeps_the_links():
    linked = DoublyLinkedList()
    first, middle, last = (linked.push_back(v) for v in (1, 2, 3))

    linked.remove(middle)
    assert (list(linked), first.next, last.prev) == ([1, 3], last, first)
    linked.remove(first)
    linked.remove(last)
    assert list(linked) == []
    linked.check_invariants()


def test_a_removed_node_or_one_of_another_list_is_refused():
    one, other = DoublyLinkedList(), DoublyLinkedList()
    node = one.push_back(1)
    foreign = other.push_back(2)
    one.remove(node)

    for wrong in (node, foreign, DoublyNode(3), "not a node"):
        with pytest.raises(ValueError):
            one.remove(wrong)
        with pytest.raises(ValueError):
            one.move_to_front(wrong)
    assert list(other) == [2]
    one.check_invariants()
    other.check_invariants()


def test_clear_detaches_every_node():
    linked = DoublyLinkedList()
    nodes = [linked.push_back(v) for v in range(3)]

    linked.clear()
    assert all(n.owner is None and n.prev is None and n.next is None for n in nodes)
    with pytest.raises(ValueError):
        linked.remove(nodes[0])


def test_capacity_one_and_full_lists():
    linked = DoublyLinkedList(capacity=1)
    linked.push_front(1)

    assert linked.is_full()
    with pytest.raises(CapacityError):
        linked.push_back(2)
    assert linked.pop_back() == 1
    linked.push_back(3)
    assert list(linked) == [3]


@pytest.mark.parametrize("capacity", [0, -1, 2.5, "3", True])
def test_an_invalid_capacity_is_refused(capacity):
    with pytest.raises(ValueError):
        DoublyLinkedList(capacity)


def test_check_invariants_detects_broken_back_links():
    linked = DoublyLinkedList()
    nodes = [linked.push_back(v) for v in range(3)]
    nodes[2].prev = nodes[0]

    with pytest.raises(InvariantError):
        linked.check_invariants()


def test_check_invariants_detects_a_wrong_size():
    linked = DoublyLinkedList()
    linked.push_back(1)
    linked._size = 2

    with pytest.raises(InvariantError):
        linked.check_invariants()


def test_the_internal_storage_is_made_of_nodes():
    linked = DoublyLinkedList()
    linked.push_back(1)

    assert not any(isinstance(v, (list, deque, dict)) for v in vars(linked).values())
    assert repr(linked) == "DoublyLinkedList([1], capacity=None)"
