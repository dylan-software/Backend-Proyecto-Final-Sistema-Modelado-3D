"""Errores de base de datos: se registran con un request_id y se responden sin detalles internos.

503 solo cuando no se puede usar la base (fallo de conexión); 500 para el resto (una consulta que la
base rechaza, un fallo del pooler...). La respuesta lleva el mismo request_id que la línea del log,
para encontrar la causa sin enseñársela al cliente.
"""

import logging
import re
import uuid

from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

logger = logging.getLogger(__name__)

# Raised when PostgreSQL is down, unreachable or rejects the query (OSError covers refused connections).
DATABASE_ERRORS = (SQLAlchemyError, OSError)

# Fallos de conexión (503): la red o el servidor no responden, la conexión se cayó o el pool se agotó.
# OSError incluye ConnectionRefusedError, socket.gaierror y TimeoutError.
CONNECTION_ERRORS = (OSError, OperationalError, InterfaceError, DisconnectionError, PoolTimeoutError)

DATABASE_UNAVAILABLE = "No se pudo acceder a la base de datos."
INTERNAL_ERROR = "Error interno del servidor."
MAX_LOGGED_MESSAGE = 300

# SQLAlchemy añade la sentencia y sus parámetros al texto del error: se corta ahí.
_SQL_DETAILS = re.compile(r"\[(?:SQL|parameters):")
_URL_CREDENTIALS = re.compile(r"(\w[\w+.-]*://)[^\s/@]+@")
_PASSWORD_PAIR = re.compile(r"(password|passwd|pwd)=\S+", re.IGNORECASE)


def new_request_id():
    return uuid.uuid4().hex[:12]


class DatabaseFailure(Exception):
    """Respuesta de un error de base de datos; la convierte en JSON el manejador de app.main."""

    def __init__(self, status_code, detail, error, request_id):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error = error
        self.request_id = request_id


def is_connection_error(exc):
    return isinstance(exc, CONNECTION_ERRORS)


def describe_database_error(exc):
    """Clase y mensaje de un error de base de datos, aptos para el log.

    Usa el error del driver si SQLAlchemy lo envuelve, se queda con la primera línea, corta la
    sentencia SQL y sus parámetros, tapa usuario:contraseña de las URL y los pares de contraseña
    (clave igual a valor), y recorta.

    Returns:
        (clase, mensaje), p. ej. ("ProgrammingError", 'prepared statement "x" already exists').
    """
    cause = getattr(exc, "orig", None) or exc
    name = type(exc).__name__
    if cause is not exc:
        name = f"{name}({type(cause).__name__})"
    lines = str(cause).strip().splitlines()
    message = _SQL_DETAILS.split(lines[0] if lines else "", maxsplit=1)[0]
    message = _URL_CREDENTIALS.sub(r"\1***@", message)
    message = _PASSWORD_PAIR.sub(r"\1=***", message)
    return name, message.strip()[:MAX_LOGGED_MESSAGE]


def database_error(exc):
    """Registra el error con un request_id y devuelve la DatabaseFailure que hay que lanzar.

    Uso: ``except DATABASE_ERRORS as exc: raise database_error(exc) from exc``.
    """
    request_id = new_request_id()
    name, message = describe_database_error(exc)
    logger.error("request_id=%s database_error=%s message=%s", request_id, name, message)
    if is_connection_error(exc):
        return DatabaseFailure(503, DATABASE_UNAVAILABLE, "database_unavailable", request_id)
    return DatabaseFailure(500, INTERNAL_ERROR, "internal_server_error", request_id)
