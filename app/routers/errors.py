from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

# Raised when PostgreSQL is down, unreachable or rejects the query (OSError covers refused connections).
DATABASE_ERRORS = (SQLAlchemyError, OSError)


def database_unavailable():
    """503 returned by the routers when the database cannot be used."""
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo acceder a la base de datos.")
