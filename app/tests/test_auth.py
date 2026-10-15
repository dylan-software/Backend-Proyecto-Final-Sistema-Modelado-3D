import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.config import Settings, get_settings
from app.main import app
from app.models.space import MuebleCatalogo
from app.models.user import Usuario
from app.routers.auth import InvalidTokenError, decode_access_token
from app.routers.spaces import debug_router, get_coherence_validator, get_layout_suggester
from app.tests.auth_helpers import make_custom_token, make_token

pytestmark = pytest.mark.anyio

REGISTER = {"nombre": "Ana", "email": " Ana@Mail.com ", "password": "contraseña-larga"}


# --- POST /auth/register and /auth/login -------------------------------------------------------


async def test_register_creates_account_without_exposing_password(client, db_sessions):
    response = await client.post("/api/v1/auth/register", json=REGISTER)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {"id": body["id"], "nombre": "Ana", "email": "ana@mail.com"}
    async with db_sessions() as session:
        user = await session.scalar(select(Usuario))
    assert user.email == "ana@mail.com"
    assert user.password_hash.startswith("scrypt$")
    assert "contraseña-larga" not in user.password_hash


async def test_register_rejects_duplicate_email(client, db_sessions):
    await client.post("/api/v1/auth/register", json=REGISTER)
    response = await client.post("/api/v1/auth/register", json={**REGISTER, "email": "ana@mail.com"})

    assert response.status_code == 409


@pytest.mark.parametrize("override", [{"password": "corta"}, {"email": "no-es-un-email"}, {"nombre": "   "}])
async def test_register_validates_input(client, db_sessions, override):
    response = await client.post("/api/v1/auth/register", json={**REGISTER, **override})

    assert response.status_code == 422


async def test_login_issues_a_token_for_the_account(client, db_sessions):
    user_id = (await client.post("/api/v1/auth/register", json=REGISTER)).json()["id"]

    response = await client.post(
        "/api/v1/auth/login", json={"email": "ANA@mail.com", "password": "contraseña-larga"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().jwt_expire_minutes * 60
    assert body["usuario"] == {"id": user_id, "nombre": "Ana", "email": "ana@mail.com"}
    payload = decode_access_token(body["access_token"], get_settings().jwt_secret)
    assert payload["sub"] == str(user_id)

    # The issued token opens the protected endpoints as that account.
    spaces = await client.get("/api/v1/spaces", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert spaces.status_code == 200
    assert spaces.json()["user_id"] == str(user_id)


@pytest.mark.parametrize(
    "credentials",
    [
        {"email": "ana@mail.com", "password": "otra-contraseña"},
        {"email": "nadie@mail.com", "password": "contraseña-larga"},
    ],
)
async def test_login_rejects_wrong_credentials_with_the_same_message(client, db_sessions, credentials):
    await client.post("/api/v1/auth/register", json=REGISTER)

    response = await client.post("/api/v1/auth/login", json=credentials)

    assert response.status_code == 401
    assert response.json()["detail"] == "Email o contraseña incorrectos."
    assert response.headers["www-authenticate"] == "Bearer"


# --- get_current_user ---------------------------------------------------------------------------


async def test_protected_endpoint_without_token_is_401(client, db_sessions):
    response = await client.get("/api/v1/spaces")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def _with_signature_of(token, other):
    """`token`'s header and payload with the signature of `other`."""
    return token.rsplit(".", 1)[0] + "." + other.rsplit(".", 1)[1]


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: make_custom_token(secret="otro-secreto"), id="signed-with-another-secret"),
        pytest.param(
            lambda: _with_signature_of(make_custom_token(sub="user-b"), make_token("user-a")),
            id="payload-altered",
        ),
        pytest.param(lambda: make_custom_token(exp=int(time.time()) - 1), id="expired"),
        pytest.param(lambda: make_custom_token(exp=None), id="exp-missing"),
        pytest.param(lambda: make_custom_token(sub=""), id="sub-empty"),
        pytest.param(lambda: make_custom_token(sub=5), id="sub-not-a-string"),
        pytest.param(lambda: "a.b", id="not-a-jwt"),
        pytest.param(lambda: "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ1c2VyLWEifQ.", id="alg-none"),
        pytest.param(lambda: "%%%.%%%.%%%", id="garbage"),
    ],
)
async def test_invalid_tokens_are_401(client, db_sessions, token):
    response = await client.get("/api/v1/spaces", headers={"Authorization": f"Bearer {token()}"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    assert response.json()["detail"] == "Token de sesión inválido o caducado."


async def test_non_bearer_scheme_is_401(client, db_sessions):
    response = await client.get("/api/v1/spaces", headers={"Authorization": f"Basic {make_token('user-a')}"})

    assert response.status_code == 401


def test_decode_rejects_alg_none_even_with_valid_claims():
    token = make_custom_token()
    header_none = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
    forged = header_none + "." + token.split(".")[1] + "."
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged, get_settings().jwt_secret)


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/v1/spaces", None),
        (
            "POST",
            "/api/v1/spaces/catalog",
            {"nombre": "Silla", "categoria": "chair", "ancho": 1, "alto": 1, "profundidad": 1},
        ),
        ("DELETE", "/api/v1/spaces/furniture/1", None),
        ("POST", "/api/v1/spaces/analyze", {"nombre": "Sala", "puntos": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]}),
        ("POST", "/geometry/point-cloud", {"points": [[0, 0, 0]]}),
        (
            "POST",
            "/geometry/collision",
            {
                "habitacion": {"ancho": 1, "alto": 1, "profundidad": 1},
                "mueble": {"min": [0, 0, 0], "max": [1, 1, 1]},
            },
        ),
    ],
)
async def test_every_data_endpoint_requires_a_token(client, db_sessions, method, path, json):
    response = await client.request(method, path, json=json)

    assert response.status_code == 401, (path, response.text)


async def test_public_endpoints_stay_open(client):
    assert (await client.get("/")).status_code == 200


# --- Ownership: the token's account always wins -------------------------------------------------


@pytest.fixture
def accept_all_names():
    async def validator(nombre):
        return True

    app.dependency_overrides[get_coherence_validator] = lambda: validator
    yield
    app.dependency_overrides.pop(get_coherence_validator, None)


CHAIR = {"nombre": "Silla", "categoria": "chair", "ancho": 50, "alto": 90, "profundidad": 50}


async def test_user_cannot_list_another_users_spaces(user_a, db_sessions):
    response = await user_a.get("/api/v1/spaces", params={"user_id": "user-b"})

    assert response.status_code == 403


async def test_user_cannot_add_catalog_items_for_another_user(user_a, db_sessions, accept_all_names):
    response = await user_a.post("/api/v1/spaces/catalog", json={**CHAIR, "user_id": "user-b"})

    assert response.status_code == 403
    async with db_sessions() as session:
        assert (await session.scalars(select(MuebleCatalogo))).all() == []


async def test_catalog_item_is_owned_by_the_token_account(user_a, db_sessions, accept_all_names):
    response = await user_a.post("/api/v1/spaces/catalog", json=CHAIR)

    assert response.status_code == 201, response.text
    async with db_sessions() as session:
        assert (await session.scalar(select(MuebleCatalogo))).user_id == "user-a"


async def test_user_cannot_delete_another_users_furniture(user_a, user_b, db_sessions, accept_all_names):
    item = (await user_b.post("/api/v1/spaces/catalog", json=CHAIR)).json()

    response = await user_a.delete(f"/api/v1/spaces/furniture/{item['id']}")

    # Same answer as a missing id: the item's existence is not revealed.
    assert response.status_code == 404
    assert response.json()["detail"] == f"No existe un mueble con id {item['id']}."
    async with db_sessions() as session:
        assert [m.id for m in (await session.scalars(select(MuebleCatalogo))).all()] == [item["id"]]
    # Its owner can still delete it.
    assert (await user_b.delete(f"/api/v1/spaces/furniture/{item['id']}")).status_code == 200


async def test_user_cannot_analyze_a_room_for_another_user(user_a, db_sessions):
    async def unused_suggester(*args, **kwargs):
        raise AssertionError("must not be called")

    app.dependency_overrides[get_layout_suggester] = lambda: unused_suggester
    try:
        response = await user_a.post(
            "/api/v1/spaces/analyze",
            json={"nombre": "Sala", "user_id": "user-b", "puntos": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]},
        )
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)

    assert response.status_code == 403


# --- Startup configuration ----------------------------------------------------------------------


def test_production_without_jwt_secret_is_not_ready():
    settings = Settings(_env_file=None, environment="production", jwt_secret="")

    assert settings.jwt_secret == ""  # no random fallback in production
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        settings.check_production_ready()


def test_production_accepts_a_fixed_secret():
    settings = Settings(_env_file=None, environment="Production", jwt_secret="fixed-secret")

    settings.check_production_ready()
    assert settings.jwt_secret == "fixed-secret"


def test_server_refuses_to_start_in_production_without_secret_and_hides_settings():
    env = {
        **os.environ,
        "ENVIRONMENT": "production",
        "JWT_SECRET": "",
        "DATABASE_URL": "postgresql://user:SECRET-DB-PASSWORD@db.example/app",
        "OMNIROUTE_API_KEY": "SECRET-API-KEY",
    }
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"], cwd=root, env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "JWT_SECRET must be set" in result.stderr
    assert "SECRET-DB-PASSWORD" not in result.stderr + result.stdout
    assert "SECRET-API-KEY" not in result.stderr + result.stdout


def test_development_generates_a_random_secret():
    first = Settings(_env_file=None, environment="development", jwt_secret="")
    second = Settings(_env_file=None, environment="development", jwt_secret="")

    assert len(first.jwt_secret) >= 32
    assert first.jwt_secret != second.jwt_secret


def test_allowed_origins_parsing():
    settings = Settings(_env_file=None, allowed_origins=" https://a.vercel.app/ , ,http://localhost:3000")

    assert settings.cors_origins == ["https://a.vercel.app", "http://localhost:3000"]
    # The environment of the tests sets ALLOWED_ORIGINS, so check the declared default directly.
    assert Settings.model_fields["allowed_origins"].default == "http://localhost:3000"


def test_debug_routes_are_off_by_default():
    assert Settings.model_fields["enable_debug_routes"].default is False
    assert not any(getattr(route, "path", "") == "/api/v1/spaces/debug-vars" for route in app.routes)


async def test_debug_route_when_enabled_reports_only_booleans():
    import httpx

    debug_app = FastAPI()
    debug_app.include_router(debug_router)
    transport = httpx.ASGITransport(app=debug_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/api/v1/spaces/debug-vars")

    assert response.status_code == 200
    assert all(isinstance(value, bool) for value in response.json().values())


async def test_unhandled_error_gets_cors_header_only_for_allowed_origins(monkeypatch):
    import httpx

    from app.routers import spaces

    def broken(body):
        raise RuntimeError("boom")

    monkeypatch.setattr(spaces, "_category_name", broken)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        headers = {"Authorization": f"Bearer {make_token('user-a')}"}
        allowed = await http.post(
            "/api/v1/spaces/catalog",
            json=CHAIR,
            headers={**headers, "Origin": "https://proyecto-3d.vercel.app"},
        )
        other = await http.post(
            "/api/v1/spaces/catalog", json=CHAIR, headers={**headers, "Origin": "https://evil.example"}
        )

    assert allowed.status_code == other.status_code == 500
    assert allowed.headers["access-control-allow-origin"] == "https://proyecto-3d.vercel.app"
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in other.headers
