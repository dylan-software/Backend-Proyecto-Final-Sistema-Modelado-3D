"""Límites de peticiones en memoria (ventanas deslizantes), que responden 429 con Retry-After.

Los contadores viven en este proceso: se reinician cuando el servidor se reinicia y no se comparten
entre workers ni instancias. La imagen de Docker ejecuta un único worker de uvicorn en una instancia
de Render, así que basta para impedir que un cliente agote la cuota de IA o adivine contraseñas; con
varios workers, cada uno permitiría el límite completo.
"""

import math
import threading
import time
from functools import lru_cache

from fastapi import HTTPException, status

from app.config import get_settings
from app.structures import LinkedQueue

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR

# Claves distintas que se guardan antes de olvidar las inactivas, para que emails o IPs aleatorios
# no hagan crecer la memoria.
MAX_KEYS = 50_000


class RateLimiter:
    """Permite como mucho `limit` eventos cada `window` segundos por clave, para cada par (limit, window).

    clock: devuelve segundos; time.monotonic por defecto, sustituible en las pruebas.
    """

    def __init__(self, limits, clock=time.monotonic):
        self._limits = [(limit, window) for limit, window in limits if limit > 0]
        self._longest = max((window for _, window in self._limits), default=0)
        self._clock = clock
        # clave -> LinkedQueue de marcas de tiempo: la más antigua al frente, la última al final.
        self._events = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        events = self._events.get(key)
        if events is None:
            return None
        # Saca por el frente (O(1) cada una) las marcas que ya salieron de la ventana más larga.
        while events and events.peek() <= now - self._longest:
            events.dequeue()
        if not events:
            del self._events[key]
            return None
        return events

    def retry_after(self, key, cost=1):
        """Segundos hasta que caben `cost` eventos más para key; 0 si caben ya.

        Si cost supera algún límite, nunca cabrían: se devuelve la ventana entera de ese límite.
        """
        with self._lock:
            now = self._clock()
            events = self._recent(key, now)
            wait = 0
            for limit, window in self._limits:
                if cost > limit:
                    wait = max(wait, window)
                    continue
                if events is None:
                    continue
                in_window = [t for t in events if t > now - window]
                excess = len(in_window) + cost - limit
                if excess > 0:
                    # El evento que debe salir de la ventana para que quepan cost más.
                    wait = max(wait, in_window[excess - 1] + window - now)
            return wait

    def record(self, key, cost=1):
        """Registra cost eventos para key en el instante actual."""
        with self._lock:
            now = self._clock()
            if key not in self._events and len(self._events) >= MAX_KEYS:
                self._forget_idle(now)
            events = self._events.get(key)
            if events is None:
                events = self._events[key] = LinkedQueue()
            for _ in range(cost):
                events.enqueue(now)  # O(1) cada una, por el final

    def reset(self, key):
        with self._lock:
            self._events.pop(key, None)

    def _forget_idle(self, now):
        for key in list(self._events):
            self._recent(key, now)
        # Sigue lleno: se olvidan las claves vistas primero (los dict conservan el orden de inserción).
        while len(self._events) >= MAX_KEYS:
            del self._events[next(iter(self._events))]


def too_many_requests(wait, detail):
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, detail, headers={"Retry-After": str(max(1, math.ceil(wait)))}
    )


@lru_cache
def ai_limiter():
    """Llamadas al modelo de IA (chat, catalog, analyze, analyze-photo), por cuenta."""
    settings = get_settings()
    return RateLimiter([(settings.ai_requests_per_minute, MINUTE), (settings.ai_requests_per_day, DAY)])


@lru_cache
def login_failure_limiter():
    """Logins fallidos por email. No por IP: la IP sale de cabeceras que un cliente puede falsificar."""
    settings = get_settings()
    return RateLimiter([(settings.login_failures_per_email, settings.login_failure_window_minutes * MINUTE)])


@lru_cache
def registration_limiter():
    """Registros, con una clave compartida para el límite global y una clave por IP del cliente."""
    settings = get_settings()
    return {
        "global": RateLimiter([(settings.registrations_per_hour, HOUR)]),
        "ip": RateLimiter([(settings.registrations_per_ip_per_hour, HOUR)]),
    }


def reset_all():
    """Olvida todos los contadores y vuelve a leer la configuración (pruebas)."""
    for factory in (ai_limiter, login_failure_limiter, registration_limiter):
        factory.cache_clear()


def enforce_ai_quota(user_id, cost=1):
    """Cuenta `cost` llamadas a la IA para la cuenta, o lanza 429 si no le queda cupo para todas.

    Se llama justo antes de llamar al modelo, después de las comprobaciones baratas (dueño, validación,
    tope del catálogo), para que las peticiones rechazadas por otro motivo no gasten cupo. Una foto
    cuesta más que un mensaje (cost=3). Sin await entre la comprobación y el registro (decisión 24).
    """
    limiter = ai_limiter()
    wait = limiter.retry_after(user_id, cost)
    if wait:
        raise too_many_requests(
            wait, "Has hecho demasiadas peticiones a la IA; espera un poco antes de volver a intentarlo."
        )
    limiter.record(user_id, cost)


def enforce_registration_limit(request):
    """Cuenta un registro, o lanza 429 si se agotó el límite por hora global o el de esta IP.

    El límite global se cumple digan lo que digan las cabeceras; el de por IP es de mejor esfuerzo
    (ver client_ip).
    """
    limiters = registration_limiter()
    keys = (("global", "all"), ("ip", client_ip(request)))
    wait = max(limiters[name].retry_after(key) for name, key in keys)
    if wait:
        raise too_many_requests(wait, "Se han creado demasiadas cuentas; vuelve a intentarlo más tarde.")
    for name, key in keys:
        limiters[name].record(key)


def client_ip(request):
    """IP del cliente, de mejor esfuerzo, para el límite de registros por IP.

    X-Forwarded-For es una lista que cualquiera puede empezar: un cliente puede enviar
    "X-Forwarded-For: 1.2.3.4" y cada proxy añade detrás lo que vio. Solo son de fiar las entradas
    que añaden nuestros propios proxies, que son las últimas TRUSTED_PROXY_HOPS. Con 0 (el valor por
    defecto) se ignora la cabecera y se usa el par TCP. En Render el par TCP es el proxy de Render, no
    el usuario, así que allí se pone TRUSTED_PROXY_HOPS=1, SUPONIENDO que el proxy de Render añade la
    IP real del cliente como última entrada. Si esa suposición fuera falsa, el límite por IP solo se
    debilita; el límite global de registros no depende de ninguna cabecera.
    """
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",")]
        forwarded = [part for part in forwarded if part]
        if len(forwarded) >= hops:
            return forwarded[-hops]
    return request.client.host if request.client else "unknown"
