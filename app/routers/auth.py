import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import get_session
from app.models.user import Usuario
from app.routers.errors import DATABASE_ERRORS, database_unavailable
from app.schemas.auth import TokenOut, UserLogin, UserOut, UserRegister

router = APIRouter(tags=["auth"])

# scrypt cost parameters (n = CPU/memory cost, r = block size, p = parallelism).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password):
    """Salted scrypt hash, stored as "scrypt$<salt hex>$<hash hex>"."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    """Check a password against a hash_password() value in constant time."""
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return hmac.compare_digest(digest, expected)


# Checked when the email does not exist, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text):
    """Inverse of _b64url. Raises ValueError (binascii.Error is a subclass) on invalid input."""
    if not text or not text.isascii():
        raise ValueError("empty or non-ASCII segment")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input, secret):
    return hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()


def encode_token(payload, secret):
    """Encode and sign a payload as an HS256 JWT."""
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, payload)
    )
    return f"{signing_input}.{_b64url(_sign(signing_input, secret))}"


def create_access_token(user):
    """Session token in JWT format (HS256) signed with JWT_SECRET.

    Returns:
        (token, expires_in): the encoded token and its lifetime in seconds.
    """
    settings = get_settings()
    expires_in = settings.jwt_expire_minutes * 60
    now = int(time.time())
    payload = {"sub": str(user.id), "email": user.email, "iat": now, "exp": now + expires_in}
    return encode_token(payload, settings.jwt_secret), expires_in


class InvalidTokenError(Exception):
    """The token is malformed, wrongly signed, expired or has no subject."""


def decode_access_token(token, secret):
    """Verify an HS256 token made by encode_token and return its payload.

    Only HS256 is accepted, so a token whose header says "none" or another algorithm is rejected.

    Raises:
        InvalidTokenError: with a short reason that is safe to log (it never contains the token).
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidTokenError("not a JWT")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        signature = _b64url_decode(signature_b64)
    except ValueError as exc:
        raise InvalidTokenError("malformed token") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise InvalidTokenError("unsupported algorithm")
    expected = _sign(f"{header_b64}.{payload_b64}", secret)
    if not hmac.compare_digest(signature, expected):
        raise InvalidTokenError("bad signature")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except ValueError as exc:
        raise InvalidTokenError("malformed payload") from exc
    if not isinstance(payload, dict):
        raise InvalidTokenError("malformed payload")
    exp = payload.get("exp")
    # bool is a subclass of int; "exp": true must not count as a timestamp.
    if not isinstance(exp, int | float) or isinstance(exp, bool) or exp <= time.time():
        raise InvalidTokenError("expired or missing exp")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise InvalidTokenError("missing sub")
    return payload


_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail):
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


async def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """FastAPI dependency: the account id (the token's "sub") of the request's Bearer token.

    Raises:
        HTTPException: 401 with WWW-Authenticate when the header is missing or the token is invalid.
    """
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Falta el token de sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials, get_settings().jwt_secret)
    except InvalidTokenError as exc:
        raise _unauthorized("Token de sesión inválido o caducado.") from exc
    return payload["sub"]


def ensure_same_user(requested, current):
    """Check an optional user_id sent by the client against the token's account.

    The frontend still sends user_id for compatibility with older backends; it is only accepted
    when it names the same account as the token.

    Returns:
        The token's account id, which is what endpoints must use.

    Raises:
        HTTPException: 403 when user_id is present and differs from the token.
    """
    if requested is not None and requested != current:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "El user_id no coincide con la sesión.")
    return current


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserRegister, session: AsyncSession = Depends(get_session)):
    """Create a user account with a hashed password.

    Returns:
        201 with the new user (without the password).

    Raises:
        HTTPException: 409 email already registered, 503 database failure.
    """
    email_taken = HTTPException(status.HTTP_409_CONFLICT, "El email ya está registrado.")
    try:
        if await session.scalar(select(Usuario.id).where(Usuario.email == body.email)) is not None:
            raise email_taken
        user = Usuario(
            nombre=body.nombre,
            email=body.email,
            password_hash=await asyncio.to_thread(hash_password, body.password),
        )
        session.add(user)
        await session.commit()
    except IntegrityError as exc:
        # Another request registered the same email between the check and the insert.
        await session.rollback()
        raise email_taken from exc
    except DATABASE_ERRORS as exc:
        await session.rollback()
        raise database_unavailable() from exc

    return UserOut(id=user.id, nombre=user.nombre, email=user.email)


@router.post("/login", response_model=TokenOut)
async def login(body: UserLogin, session: AsyncSession = Depends(get_session)):
    """Check email and password and issue a session token.

    Returns:
        200 with the bearer token, its lifetime in seconds and the user.

    Raises:
        HTTPException: 401 wrong email or password (same message for both), 503 database failure.
    """
    try:
        user = await session.scalar(select(Usuario).where(Usuario.email == body.email))
    except DATABASE_ERRORS as exc:
        raise database_unavailable() from exc
    # scrypt is CPU-bound; run it in a worker thread so other requests keep being served.
    valid = await asyncio.to_thread(
        verify_password, body.password, user.password_hash if user else _DUMMY_HASH
    )
    if user is None or not valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Email o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(user)
    return TokenOut(
        access_token=token,
        expires_in=expires_in,
        usuario=UserOut(id=user.id, nombre=user.nombre, email=user.email),
    )
