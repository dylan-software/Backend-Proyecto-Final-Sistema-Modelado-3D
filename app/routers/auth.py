import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_session
from app.models.user import Usuario
from app.schemas.auth import UserOut, UserRegister

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# scrypt cost parameters (n = CPU/memory cost, r = block size, p = parallelism).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password):
    """Salted scrypt hash, stored as "scrypt$<salt hex>$<hash hex>"."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserRegister, session: AsyncSession = Depends(get_session)):
    """Create a user account with a hashed password.

    Returns:
        201 with the new user (without the password).

    Raises:
        HTTPException: 409 email already registered, 503 database failure.
    """
    existing = await session.scalar(select(Usuario.id).where(Usuario.email == body.email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "El email ya está registrado.")

    user = Usuario(nombre=body.nombre, email=body.email, password_hash=hash_password(body.password))
    try:
        session.add(user)
        await session.commit()
    except IntegrityError as exc:
        # Another request registered the same email between the check and the insert.
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "El email ya está registrado.") from exc
    except (SQLAlchemyError, OSError) as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo guardar en la base de datos."
        ) from exc

    return UserOut(id=user.id, nombre=user.nombre, email=user.email)
