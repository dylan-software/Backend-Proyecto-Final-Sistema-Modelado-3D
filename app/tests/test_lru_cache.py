"""LruCache frente a una LRU de referencia hecha con OrderedDict (solo en las pruebas), con miles de
operaciones aleatorias, y casos límite."""

import random
from collections import OrderedDict

import pytest

from app.structures import InvariantError, LruCache

OPERATIONS = 5000
MISSING = object()


class ReferenceLru:
    """La misma política con OrderedDict: el final es lo más reciente."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()
        self.hits = self.misses = 0

    def get(self, key):
        if key not in self.data:
            self.misses += 1
            return MISSING
        self.hits += 1
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        if key in self.data:
            self.data.move_to_end(key)
        elif len(self.data) >= self.capacity:
            self.data.popitem(last=False)
        self.data[key] = value

    def discard(self, key):
        return self.data.pop(key, MISSING) is not MISSING


def assert_same(cache, reference):
    cache.check_invariants()
    assert len(cache) == len(reference.data)
    assert list(cache.items()) == list(reversed(reference.data.items()))
    assert (cache.hits, cache.misses) == (reference.hits, reference.misses)


@pytest.mark.parametrize(("seed", "capacity"), [(1, 1), (2, 3), (3, 16), (4, 64), (5, 500)])
def test_random_operations_match_ordered_dict(seed, capacity):
    rng = random.Random(seed)
    cache, reference = LruCache(capacity), ReferenceLru(capacity)
    # Más claves que capacidad, para que haya expulsiones; pocas, para que haya aciertos.
    universe = max(4, capacity * 2)
    for step in range(OPERATIONS):
        key = rng.randrange(universe)
        action = rng.random()
        if action < 0.45:
            assert cache.get(key, MISSING) == reference.get(key)
        elif action < 0.9:
            cache.put(key, step)
            reference.put(key, step)
        elif action < 0.98:
            assert cache.discard(key) == reference.discard(key)
        else:
            assert cache.peek(key, MISSING) == reference.data.get(key, MISSING)
            assert (key in cache) == (key in reference.data)
        assert_same(cache, reference)


def test_get_returns_values_and_counts():
    cache = LruCache(2)
    assert cache.get("sofa") is None
    cache.put("sofa", True)
    assert cache.get("sofa") is True
    assert cache.get("mesa", "nada") == "nada"
    assert (cache.hits, cache.misses) == (1, 2)


def test_the_least_recently_used_entry_is_evicted():
    cache = LruCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # "b" pasa a ser la menos usada
    cache.put("c", 3)
    assert list(cache.keys()) == ["c", "a"]
    assert "b" not in cache
    cache.check_invariants()


def test_put_on_an_existing_key_replaces_and_refreshes_without_evicting():
    cache = LruCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    assert list(cache.items()) == [("a", 10), ("b", 2)]
    cache.put("c", 3)
    assert list(cache.keys()) == ["c", "a"]


def test_peek_and_contains_do_not_reorder_or_count():
    cache = LruCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.peek("a") == 1
    assert "a" in cache
    cache.put("c", 3)  # "a" sigue siendo la menos usada
    assert "a" not in cache
    assert (cache.hits, cache.misses) == (0, 0)


def test_capacity_one():
    cache = LruCache(1)
    cache.put("a", True)
    cache.put("b", False)
    assert list(cache.items()) == [("b", False)]
    cache.check_invariants()


def test_false_and_none_values_are_stored():
    cache = LruCache(3)
    cache.put("a", False)
    cache.put("b", None)
    assert cache.get("a", MISSING) is False
    assert cache.get("b", MISSING) is None
    assert cache.hits == 2


def test_discard_and_clear():
    cache = LruCache(3)
    assert cache.discard("x") is False
    cache.put("x", 1)
    cache.get("x")
    assert cache.discard("x") is True
    assert len(cache) == 0
    cache.put("y", 2)
    cache.get("z")
    cache.clear()
    assert (len(cache), cache.hits, cache.misses, list(cache.keys())) == (0, 0, 0, [])
    cache.check_invariants()


@pytest.mark.parametrize("capacity", [None, 0, -1, 1.5, True, "10"])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError):
        LruCache(capacity)


def test_check_invariants_detects_a_dict_out_of_sync():
    cache = LruCache(3)
    cache.put("a", 1)
    cache.put("b", 2)
    del cache._nodes["a"]
    with pytest.raises(InvariantError):
        cache.check_invariants()


def test_check_invariants_detects_a_key_pointing_to_another_node():
    cache = LruCache(3)
    cache.put("a", 1)
    cache.put("b", 2)
    cache._nodes["a"], cache._nodes["b"] = cache._nodes["b"], cache._nodes["a"]
    with pytest.raises(InvariantError):
        cache.check_invariants()
