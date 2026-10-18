"""In-memory request limits (sliding windows), answered with 429 and Retry-After.

The counters live in this process: they reset when the server restarts and are not shared between
workers or instances. The Docker image runs a single uvicorn worker on one Render instance, so that
is enough to stop a client from burning the AI quota or guessing passwords; several workers would
each allow the full limit.
"""

import math
import threading
import time
from collections import deque
from functools import lru_cache

from fastapi import HTTPException, status

from app.config import get_settings

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR

# Distinct keys kept before the idle ones are dropped, so random emails or IPs cannot grow memory.
MAX_KEYS = 50_000


class RateLimiter:
    """Allows at most `limit` events per `window` seconds for each key, for every (limit, window) pair.

    clock: returns seconds; time.monotonic by default, replaceable in tests.
    """

    def __init__(self, limits, clock=time.monotonic):
        self._limits = [(limit, window) for limit, window in limits if limit > 0]
        self._longest = max((window for _, window in self._limits), default=0)
        self._clock = clock
        self._events = {}  # key -> deque of timestamps, oldest first
        self._lock = threading.Lock()

    def _recent(self, key, now):
        events = self._events.get(key)
        if events is None:
            return None
        while events and events[0] <= now - self._longest:
            events.popleft()
        if not events:
            del self._events[key]
            return None
        return events

    def retry_after(self, key):
        """Seconds until one more event for key is allowed; 0 if it is allowed now."""
        with self._lock:
            now = self._clock()
            events = self._recent(key, now)
            if events is None:
                return 0
            wait = 0
            for limit, window in self._limits:
                in_window = [t for t in events if t > now - window]
                if len(in_window) >= limit:
                    # The event that must leave the window for one more to fit.
                    wait = max(wait, in_window[-limit] + window - now)
            return wait

    def record(self, key):
        with self._lock:
            now = self._clock()
            if key not in self._events and len(self._events) >= MAX_KEYS:
                self._forget_idle(now)
            self._events.setdefault(key, deque()).append(now)

    def reset(self, key):
        with self._lock:
            self._events.pop(key, None)

    def _forget_idle(self, now):
        for key in list(self._events):
            self._recent(key, now)
        # Still full: drop the keys seen first (dicts keep insertion order).
        while len(self._events) >= MAX_KEYS:
            del self._events[next(iter(self._events))]


def too_many_requests(wait, detail):
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, detail, headers={"Retry-After": str(max(1, math.ceil(wait)))}
    )


@lru_cache
def ai_limiter():
    """Calls to the AI model (chat, catalog, analyze, analyze-photo), per account."""
    settings = get_settings()
    return RateLimiter([(settings.ai_requests_per_minute, MINUTE), (settings.ai_requests_per_day, DAY)])


@lru_cache
def login_failure_limiter():
    """Failed logins per email. Not per IP: an IP comes from headers a client can forge."""
    settings = get_settings()
    return RateLimiter([(settings.login_failures_per_email, settings.login_failure_window_minutes * MINUTE)])


def reset_all():
    """Forget every counter and re-read the settings (tests)."""
    for factory in (ai_limiter, login_failure_limiter):
        factory.cache_clear()


def enforce_ai_quota(user_id):
    """Count one AI call for the account, or raise 429 if it has used its quota.

    Call it right before the model is called, after the cheap checks (ownership, validation, catalog
    limit), so requests rejected for other reasons do not use the quota.
    """
    limiter = ai_limiter()
    wait = limiter.retry_after(user_id)
    if wait:
        raise too_many_requests(
            wait, "Has hecho demasiadas peticiones a la IA; espera un poco antes de volver a intentarlo."
        )
    limiter.record(user_id)
