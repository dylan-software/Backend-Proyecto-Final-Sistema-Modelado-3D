"""Integración de LruCache con el validador de coherencia de POST /catalog.

Aquí NO se sustituye get_coherence_validator: se usa el validador real con caché y solo se cambia
chat_completion por uno falso que cuenta las llamadas al modelo.
"""

import pytest

from app.config import Settings, get_settings
from app.routers.spaces import get_coherence_validator
from app.services import semantic_filter
from app.services.omniroute import OmniRouteError

pytestmark = pytest.mark.anyio

CATALOG_URL = "/api/v1/spaces/catalog"


def catalog_item(nombre):
    return {"nombre": nombre, "categoria": "Sillas", "ancho": 0.5, "alto": 0.9, "profundidad": 0.5}


@pytest.fixture(autouse=True)
def fresh_cache():
    """Cada prueba empieza con la caché vacía y relee COHERENCE_CACHE_SIZE."""
    semantic_filter.get_coherence_cache.cache_clear()
    yield
    semantic_filter.get_coherence_cache.cache_clear()


@pytest.fixture
def model(monkeypatch):
    """Modelo falso: TRUE para sofás y sillas, FALSE para lo demás. Guarda el nombre de cada llamada.

    Si model["fail"] es True, la siguiente llamada falla como OmniRoute.
    """
    state = {"calls": [], "fail": False}

    async def fake_chat_completion(messages, temperature):
        state["calls"].append(messages[1]["content"])
        if state["fail"]:
            state["fail"] = False
            raise OmniRouteError("request failed: timeout")
        content = messages[1]["content"].lower()
        return "TRUE" if "sofa" in content or "silla" in content else "FALSE"

    monkeypatch.setattr(semantic_filter, "chat_completion", fake_chat_completion)
    return state


def test_the_dependency_is_the_cached_validator():
    assert get_coherence_validator() is semantic_filter.cached_furniture_coherence


def test_the_cache_size_defaults_to_1000():
    assert Settings.model_fields["coherence_cache_size"].default == 1000
    assert semantic_filter.get_coherence_cache().capacity == get_settings().coherence_cache_size


@pytest.mark.parametrize(
    ("name", "key"),
    [("Sofa", "sofa"), ("  Silla   ROJA ", "silla roja"), ("Año", "año"), ("Café", "café")],
)
def test_cache_key_normalization(name, key):
    assert semantic_filter.coherence_cache_key(name) == key


async def test_saving_the_same_name_twice_calls_the_model_once(user_a, db_sessions, model):
    first = await user_a.post(CATALOG_URL, json=catalog_item("Sofa"))
    second = await user_a.post(CATALOG_URL, json=catalog_item("Sofa"))

    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert model["calls"] == ['"Sofa"']
    cache = semantic_filter.get_coherence_cache()
    assert (cache.hits, cache.misses) == (1, 1)
    assert list(cache.items()) == [("sofa", True)]


async def test_names_that_normalize_alike_share_the_answer(user_a, user_b, db_sessions, model):
    for client, name in ((user_a, "Sofa"), (user_a, "  SOFA "), (user_b, "sofa")):
        assert (await client.post(CATALOG_URL, json=catalog_item(name))).status_code == 201

    assert len(model["calls"]) == 1


async def test_a_rejected_name_is_cached_too(user_a, db_sessions, model):
    for _ in range(2):
        response = await user_a.post(CATALOG_URL, json=catalog_item("Algoritmo"))
        assert response.status_code == 422
        assert "Algoritmo" in response.json()["detail"]

    assert len(model["calls"]) == 1
    assert list(semantic_filter.get_coherence_cache().items()) == [("algoritmo", False)]


async def test_model_errors_are_not_cached(user_a, db_sessions, model):
    model["fail"] = True

    failed = await user_a.post(CATALOG_URL, json=catalog_item("Sofa"))
    assert failed.status_code == 502
    assert len(semantic_filter.get_coherence_cache()) == 0

    retried = await user_a.post(CATALOG_URL, json=catalog_item("Sofa"))
    assert retried.status_code == 201, retried.text
    assert len(model["calls"]) == 2


async def test_the_least_used_name_is_evicted(user_a, db_sessions, model, monkeypatch):
    monkeypatch.setattr(get_settings(), "coherence_cache_size", 2)
    semantic_filter.get_coherence_cache.cache_clear()

    for name in ("Sofa", "Silla", "Sofa", "Silla de oficina", "Silla"):
        assert (await user_a.post(CATALOG_URL, json=catalog_item(name))).status_code == 201

    # "Silla" fue la menos usada al entrar "Silla de oficina" y se expulsó: se pregunta otra vez.
    assert model["calls"] == ['"Sofa"', '"Silla"', '"Silla de oficina"', '"Silla"']
    assert list(semantic_filter.get_coherence_cache().keys()) == ["silla", "silla de oficina"]


async def test_a_cache_hit_still_counts_for_the_ai_quota(user_a, db_sessions, model, monkeypatch):
    # Comportamiento sin cambios: la cuota se comprueba antes del validador, con o sin caché.
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", 2)

    statuses = [(await user_a.post(CATALOG_URL, json=catalog_item("Sofa"))).status_code for _ in range(3)]

    assert statuses == [201, 201, 429]
    assert len(model["calls"]) == 1
