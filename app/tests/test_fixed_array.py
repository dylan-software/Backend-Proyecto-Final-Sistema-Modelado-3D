"""FixedArray frente a una list de Python de tamaño fijo (solo como referencia)."""

import random

import pytest

from app.structures import CapacityError, FixedArray, InvariantError

OPERATIONS = 5000


def assert_same(array, reference, size):
    array.check_invariants()
    assert len(array) == array.size == size == len(reference)
    assert list(array) == reference


def in_range(index, size):
    # La referencia es una list: sus índices negativos sí cuentan desde el final, FixedArray no.
    return 0 <= index < size


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_operations_match_a_list(seed):
    rng = random.Random(seed)
    size = rng.randint(1, 20)
    default = rng.choice([None, 0, "x"])
    array, reference = FixedArray(size, default), [default] * size

    for _ in range(OPERATIONS):
        action = rng.random()
        index = rng.randint(-3, size + 2)  # incluye negativos y fuera de rango por arriba
        value = rng.randint(0, 15)  # pocos valores, para que index_of acierte y falle
        if action < 0.35:
            if in_range(index, size):
                assert array.get(index) == array[index] == reference[index]
            else:
                with pytest.raises(IndexError):
                    array.get(index)
                with pytest.raises(IndexError):
                    array[index]
        elif action < 0.75:
            if in_range(index, size):
                if rng.random() < 0.5:
                    array.set(index, value)
                else:
                    array[index] = value
                reference[index] = value
            else:
                with pytest.raises(IndexError):
                    array.set(index, value)
                with pytest.raises(IndexError):
                    array[index] = value
        elif action < 0.97:
            expected = reference.index(value) if value in reference else -1
            assert array.index_of(value) == expected
        else:
            array.fill(value)
            reference[:] = [value] * size
        assert_same(array, reference, size)


@pytest.mark.parametrize("seed", [30, 31, 32, 33, 34])
def test_insert_at_and_remove_at_shift_like_a_list(seed):
    rng = random.Random(seed)
    size = rng.randint(1, 12)
    default = rng.choice([None, -1, "libre"])
    array, reference = FixedArray(size, default), [default] * size

    for _ in range(OPERATIONS):
        index = rng.randint(-2, size + 1)
        value = rng.randint(0, 15)
        if rng.random() < 0.5:
            if not in_range(index, size):
                with pytest.raises(IndexError):
                    array.insert_at(index, value)
            elif reference[-1] != default:
                with pytest.raises(CapacityError):
                    array.insert_at(index, value)
            else:
                array.insert_at(index, value)
                reference.insert(index, value)
                reference.pop()  # la última casilla (libre) sale por el final
        elif in_range(index, size):
            assert array.remove_at(index) == reference.pop(index)
            reference.append(default)
        else:
            with pytest.raises(IndexError):
                array.remove_at(index)
        assert_same(array, reference, size)


# --- Casos límite --------------------------------------------------------------------------------


def test_insert_at_shifts_to_the_right_inside_the_same_storage():
    array = FixedArray(5)
    storage = array._slots
    array.insert_at(0, "c")
    array.insert_at(0, "a")
    array.insert_at(1, "b")
    array.insert_at(3, "d")

    assert list(array) == ["a", "b", "c", "d", None]
    array.insert_at(4, "e")  # el último índice también vale si está libre
    assert list(array) == ["a", "b", "c", "d", "e"]
    assert array._slots is storage and len(array) == 5
    array.check_invariants()


def test_insert_at_with_the_last_slot_taken_raises_and_changes_nothing():
    array = FixedArray(3, default=0)
    array[2] = 7

    with pytest.raises(CapacityError):
        array.insert_at(0, 1)
    assert list(array) == [0, 0, 7]
    array.remove_at(0)  # deja libre la última casilla
    array.insert_at(0, 1)
    assert list(array) == [1, 0, 7]


def test_remove_at_shifts_to_the_left_and_refills_with_the_default():
    array = FixedArray(4, default="-")
    for index, value in enumerate("abcd"):
        array[index] = value

    assert array.remove_at(1) == "b"
    assert list(array) == ["a", "c", "d", "-"]
    assert array.remove_at(2) == "d"
    assert array.remove_at(0) == "a"
    assert list(array) == ["c", "-", "-", "-"]
    assert array.remove_at(3) == "-"  # quitar una casilla libre devuelve el default
    array.check_invariants()


def test_a_single_slot_array():
    array = FixedArray(1)
    array.insert_at(0, "x")

    with pytest.raises(CapacityError):
        array.insert_at(0, "y")
    assert array.remove_at(0) == "x"
    assert list(array) == [None]


@pytest.mark.parametrize("index", [-1, 3, 1.0, True])
def test_insert_at_and_remove_at_check_the_index(index):
    array = FixedArray(3, default=0)
    error = IndexError if isinstance(index, int) and not isinstance(index, bool) else TypeError

    with pytest.raises(error):
        array.insert_at(index, 5)
    with pytest.raises(error):
        array.remove_at(index)
    assert list(array) == [0, 0, 0]


@pytest.mark.parametrize("size", [0, -1, 2.5, "3", True, None])
def test_an_invalid_size_is_refused(size):
    with pytest.raises(ValueError):
        FixedArray(size)


def test_a_new_array_holds_the_default():
    assert list(FixedArray(3)) == [None, None, None]
    assert list(FixedArray(2, default=0)) == [0, 0]
    array = FixedArray(1, default="a")
    assert (len(array), array.size, array[0]) == (1, 1, "a")
    array.check_invariants()


@pytest.mark.parametrize("index", [-1, -3])
def test_a_negative_index_does_not_count_from_the_end(index):
    array = FixedArray(3, default=0)
    array[2] = 9

    for operation in (lambda: array.get(index), lambda: array[index]):
        with pytest.raises(IndexError):
            operation()
    for operation in (lambda: array.set(index, 1), lambda: array.__setitem__(index, 1)):
        with pytest.raises(IndexError):
            operation()
    assert list(array) == [0, 0, 9]  # el intento fallido no cambia nada


@pytest.mark.parametrize("index", [3, 4, 1000])
def test_an_index_past_the_end_is_refused(index):
    array = FixedArray(3)

    with pytest.raises(IndexError):
        array[index]
    with pytest.raises(IndexError):
        array[index] = 1


@pytest.mark.parametrize("index", [1.0, "0", True, False, None, slice(0, 1)])
def test_a_non_integer_index_is_refused(index):
    array = FixedArray(3, default=0)

    for operation in (lambda: array.get(index), lambda: array[index]):
        with pytest.raises(TypeError):
            operation()
    for operation in (lambda: array.set(index, 1), lambda: array.__setitem__(index, 1)):
        with pytest.raises(TypeError):
            operation()
    assert list(array) == [0, 0, 0]


def test_the_size_never_changes():
    array = FixedArray(4)
    storage = array._slots

    for value in range(50):
        array[value % 4] = value
        array.fill(value)
        assert (len(array), array.size, len(array._slots)) == (4, 4, 4)
    assert array._slots is storage  # fill escribe en la misma reserva, no crea otra
    for missing in ("append", "insert", "pop", "extend", "remove", "clear"):
        assert not hasattr(array, missing)
    with pytest.raises(AttributeError):
        array.size = 10  # la propiedad es de solo lectura


def test_index_of():
    array = FixedArray(5)
    for index, value in enumerate(["a", "b", "a", "c"]):
        array[index] = value

    assert array.index_of("a") == 0  # la primera aparición
    assert array.index_of("c") == 3
    assert array.index_of(None) == 4  # la casilla sin escribir guarda el default
    assert array.index_of("z") == -1


def test_fill():
    array = FixedArray(3)
    array.fill(7)

    assert list(array) == [7, 7, 7]
    assert array.index_of(None) == -1


def test_iterating_does_not_consume_the_array():
    array = FixedArray(3, default=0)
    array[1] = "b"

    assert list(array) == list(array) == [0, "b", 0]
    assert repr(array) == "FixedArray([0, 'b', 0], size=3)"


def test_check_invariants_detects_a_storage_that_grew_or_shrank():
    array = FixedArray(3)
    array.check_invariants()

    array._slots.append(1)
    with pytest.raises(InvariantError):
        array.check_invariants()

    array._slots.pop()
    array._slots.pop()
    with pytest.raises(InvariantError):
        array.check_invariants()


def test_the_internal_storage_is_one_preallocated_list():
    array = FixedArray(6)

    assert isinstance(array._slots, list)
    assert len(array._slots) == 6
