"""RateLimiter guarda sus marcas en LinkedQueue y responde igual que una referencia con listas."""

import random

import pytest

from app.structures import LinkedQueue
from app.utils.rate_limit import DAY, MINUTE, RateLimiter

OPERATIONS = 5000


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def reference_wait(timestamps, limits, now, cost):
    """El mismo cálculo de retry_after sobre una list con TODAS las marcas (sin purgar nunca)."""
    wait = 0
    for limit, window in limits:
        if cost > limit:
            wait = max(wait, window)
            continue
        in_window = [t for t in timestamps if t > now - window]
        excess = len(in_window) + cost - limit
        if excess > 0:
            wait = max(wait, in_window[excess - 1] + window - now)
    return wait


def assert_queues_are_sound(limiter):
    for events in limiter._events.values():
        assert isinstance(events, LinkedQueue)
        events.check_invariants()
        stamps = list(events)
        assert stamps, "una clave sin marcas debe borrarse"
        assert stamps == sorted(stamps)  # la más antigua al frente


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_operations_match_a_list_reference(seed):
    rng = random.Random(seed)
    limits = [(rng.randint(1, 6), MINUTE), (rng.randint(6, 20), DAY)]
    clock = Clock()
    limiter = RateLimiter(limits, clock=clock)
    reference = {}  # clave -> list de todas las marcas registradas

    for _ in range(OPERATIONS):
        key = rng.choice("abcd")
        cost = rng.randint(1, 3)
        action = rng.random()
        if action < 0.45:
            expected = reference_wait(reference.get(key, []), limits, clock.now, cost)
            assert limiter.retry_after(key, cost) == pytest.approx(expected)
        elif action < 0.8:
            # Como enforce_ai_quota: solo se registra si cabe.
            if reference_wait(reference.get(key, []), limits, clock.now, cost) == 0:
                limiter.record(key, cost)
                reference.setdefault(key, []).extend([clock.now] * cost)
        elif action < 0.83:
            limiter.reset(key)
            reference.pop(key, None)
        else:
            clock.now += rng.choice([1, 7, 30, MINUTE, 3 * MINUTE, DAY // 4])
        assert_queues_are_sound(limiter)


def test_a_new_key_gets_a_linked_queue_with_one_mark_per_unit_of_cost():
    clock = Clock()
    limiter = RateLimiter([(10, MINUTE)], clock=clock)
    limiter.record("a", cost=3)

    events = limiter._events["a"]
    assert isinstance(events, LinkedQueue)
    assert list(events) == [1000.0, 1000.0, 1000.0]
    events.check_invariants()


def test_old_marks_leave_from_the_front_and_an_empty_key_is_dropped():
    clock = Clock()
    limiter = RateLimiter([(5, MINUTE)], clock=clock)
    limiter.record("a")
    clock.now += 30
    limiter.record("a")
    clock.now += 40  # la primera (t=1000) ya salió de la ventana; la segunda (t=1030) no

    assert limiter.retry_after("a") == 0
    assert list(limiter._events["a"]) == [1030.0]
    clock.now += MINUTE
    assert limiter.retry_after("a") == 0
    assert "a" not in limiter._events
