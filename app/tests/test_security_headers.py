"""Cabecera Strict-Transport-Security (HSTS) en las respuestas del backend."""

import httpx
import pytest

from app.main import app
from app.services.project_import import MAX_IMPORT_BYTES
from app.tests.auth_helpers import auth_headers
from app.utils.middlewares import HSTS_VALUE, StrictTransportSecurityMiddleware

pytestmark = pytest.mark.anyio

ALLOWED_ORIGIN = "https://proyecto-3d.vercel.app"
CHAIR = {"nombre": "Silla", "categoria": "chair", "ancho": 50, "alto": 90, "profundidad": 50}


def test_hsts_value_is_one_year_with_subdomains_and_without_preload():
    assert HSTS_VALUE == "max-age=31536000; includeSubDomains"
    assert "preload" not in HSTS_VALUE


async def test_health_answer_has_hsts(client):
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == HSTS_VALUE


async def test_error_answers_have_hsts(client):
    unauthorized = await client.get("/api/v1/spaces")
    invalid = await client.post("/api/v1/auth/login", json={"email": "no", "password": ""})

    assert unauthorized.status_code == 401
    assert invalid.status_code == 422
    for response in (unauthorized, invalid):
        assert response.headers["strict-transport-security"] == HSTS_VALUE


async def test_body_too_large_answer_has_hsts(user_a):
    # BodySizeLimitMiddleware responde 413 antes de llegar a FastAPI; la cabecera se añade por fuera.
    response = await user_a.post(
        "/api/v1/spaces/rooms/import",
        content=b"x" * (MAX_IMPORT_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["strict-transport-security"] == HSTS_VALUE


async def test_cors_preflight_keeps_its_headers_and_gets_hsts(client):
    response = await client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["strict-transport-security"] == HSTS_VALUE


async def test_unhandled_error_answer_has_hsts(monkeypatch):
    from app.routers import spaces

    def broken(body):
        raise RuntimeError("boom")

    monkeypatch.setattr(spaces, "_category_name", broken)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/api/v1/spaces/catalog",
            json=CHAIR,
            headers={**auth_headers("user-a"), "Origin": ALLOWED_ORIGIN},
        )

    assert response.status_code == 500
    assert response.headers["strict-transport-security"] == HSTS_VALUE
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


async def _run(middleware, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


async def test_middleware_does_not_duplicate_an_existing_header():
    async def inner(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"strict-transport-security", b"max-age=60")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    sent = await _run(StrictTransportSecurityMiddleware(inner), {"type": "http"})

    headers = sent[0]["headers"]
    assert headers == [(b"strict-transport-security", b"max-age=60")]
    assert sent[1] == {"type": "http.response.body", "body": b""}


async def test_middleware_leaves_non_http_scopes_alone():
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["type"])
        await send({"type": "lifespan.startup.complete"})

    sent = await _run(StrictTransportSecurityMiddleware(inner), {"type": "lifespan"})

    assert calls == ["lifespan"]
    assert sent == [{"type": "lifespan.startup.complete"}]
