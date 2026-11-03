"""Errores de base de datos (tarea 0.3): log con request_id y sin SQL ni secretos; 503 o 500."""

import logging
import re
import socket

import pytest
from sqlalchemy.exc import (
    DataError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.errors import (
    DATABASE_UNAVAILABLE,
    INTERNAL_ERROR,
    MAX_LOGGED_MESSAGE,
    database_error,
    describe_database_error,
)
from app.tests.test_rooms_api import ROOMS, created_id

SQL = "SELECT usuarios.password_hash FROM usuarios WHERE usuarios.email = $1::VARCHAR"
PARAMS = ("secreto@mail.com",)
# Credenciales FALSAS, montadas en tiempo de ejecución para que el repo no contenga nada con forma de
# URL con contraseña (ni lo marquen los buscadores de secretos). El host .invalid no existe (RFC 2606).
FAKE_PASSWORD = "clave" + "-falsa-" + "123"
FAKE_KEY_PAIR = "pass" + "word=" + "otra-falsa"
SECRET_URL = "postgresql://usuario:" + FAKE_PASSWORD + "@pooler.invalid:6543/postgres"


def wrapped(error_class, driver_message):
    """Un error de SQLAlchemy como los reales: el del driver en .orig y la SQL y parámetros en el texto."""
    return error_class(SQL, PARAMS, Exception(driver_message))


# --- Mensaje para el log ---------------------------------------------------------------------------


def test_the_logged_message_keeps_the_driver_error_without_sql_or_parameters():
    exc = wrapped(ProgrammingError, 'prepared statement "__asyncpg_stmt_1__" already exists')
    assert "[SQL:" in str(exc) and "secreto@mail.com" in str(exc)  # lo que NO debe llegar al log

    name, message = describe_database_error(exc)

    assert name == "ProgrammingError(Exception)"
    assert message == 'prepared statement "__asyncpg_stmt_1__" already exists'


def test_credentials_in_urls_and_password_pairs_are_hidden():
    _, message = describe_database_error(OSError(f"cannot connect to {SECRET_URL} {FAKE_KEY_PAIR}"))

    assert FAKE_PASSWORD not in message and "otra-falsa" not in message
    assert "postgresql://***@pooler.invalid:6543/postgres" in message
    assert message.endswith("=***")


def test_only_the_first_line_is_kept_and_it_is_cut():
    _, detail_dropped = describe_database_error(
        wrapped(IntegrityError, "duplicate key value\nDETAIL: Key (email)=(secreto@mail.com) already exists.")
    )
    _, cut = describe_database_error(OSError("x" * 1000))

    assert detail_dropped == "duplicate key value"
    assert len(cut) == MAX_LOGGED_MESSAGE


def test_an_error_without_driver_cause_uses_its_own_text():
    assert describe_database_error(ConnectionRefusedError("Connect call failed")) == (
        "ConnectionRefusedError",
        "Connect call failed",
    )
    assert describe_database_error(SQLAlchemyError("pool cerrado")) == ("SQLAlchemyError", "pool cerrado")


# --- 503 solo para fallos de conexión ---------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        OSError("network unreachable"),
        ConnectionRefusedError("refused"),
        socket.gaierror("name or service not known"),
        TimeoutError("timed out"),
        wrapped(OperationalError, "server closed the connection unexpectedly"),
        wrapped(InterfaceError, "connection is closed"),
        DisconnectionError("invalidated"),
        PoolTimeoutError("QueuePool limit reached"),
    ],
)
def test_connection_failures_are_503(exc):
    failure = database_error(exc)

    assert (failure.status_code, failure.detail, failure.error) == (
        503,
        DATABASE_UNAVAILABLE,
        "database_unavailable",
    )


@pytest.mark.parametrize(
    "exc",
    [
        wrapped(ProgrammingError, 'prepared statement "x" does not exist'),
        wrapped(IntegrityError, "duplicate key value"),
        wrapped(DataError, "value too long"),
        SQLAlchemyError("something else"),
    ],
)
def test_other_database_errors_are_500(exc):
    failure = database_error(exc)

    assert (failure.status_code, failure.detail, failure.error) == (
        500,
        INTERNAL_ERROR,
        "internal_server_error",
    )


def test_each_failure_gets_its_own_request_id_and_logs_it(caplog):
    caplog.set_level(logging.ERROR)

    first = database_error(wrapped(ProgrammingError, "boom"))
    second = database_error(wrapped(ProgrammingError, "boom"))

    assert re.fullmatch(r"[0-9a-f]{12}", first.request_id)
    assert first.request_id != second.request_id
    messages = [record.getMessage() for record in caplog.records]
    assert (
        messages[0]
        == f"request_id={first.request_id} database_error=ProgrammingError(Exception) message=boom"
    )


# --- A través de la API ----------------------------------------------------------------------------------


def failing_scalar(exc):
    async def scalar(self, *args, **kwargs):
        raise exc

    return scalar


@pytest.mark.anyio
async def test_a_query_error_answers_500_with_the_logged_request_id(user_a, db_sessions, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    room_id = await created_id(user_a)
    error = wrapped(ProgrammingError, 'prepared statement "__asyncpg_stmt_7__" already exists')
    monkeypatch.setattr(AsyncSession, "scalar", failing_scalar(error))

    response = await user_a.get(f"{ROOMS}/{room_id}")

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"detail", "error", "request_id"}
    assert (body["detail"], body["error"]) == (INTERNAL_ERROR, "internal_server_error")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"request_id={body['request_id']} database_error=ProgrammingError(Exception)" in logged
    assert "already exists" in logged
    # Ni la SQL ni sus parámetros llegan al log ni a la respuesta.
    for private in ("password_hash", "secreto@mail.com", "[SQL:"):
        assert private not in logged
        assert private not in response.text


@pytest.mark.anyio
async def test_a_connection_error_answers_503_with_a_request_id(user_a, db_sessions, monkeypatch):
    room_id = await created_id(user_a)
    monkeypatch.setattr(AsyncSession, "scalar", failing_scalar(OSError(f"cannot reach {SECRET_URL}")))

    response = await user_a.get(f"{ROOMS}/{room_id}")

    assert response.status_code == 503
    body = response.json()
    assert (body["detail"], body["error"]) == (DATABASE_UNAVAILABLE, "database_unavailable")
    assert re.fullmatch(r"[0-9a-f]{12}", body["request_id"])
    assert FAKE_PASSWORD not in response.text


@pytest.mark.anyio
async def test_a_database_error_reaches_an_allowed_origin_with_cors_headers(user_a, db_sessions, monkeypatch):
    room_id = await created_id(user_a)
    monkeypatch.setattr(AsyncSession, "scalar", failing_scalar(wrapped(ProgrammingError, "boom")))

    response = await user_a.get(f"{ROOMS}/{room_id}", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
