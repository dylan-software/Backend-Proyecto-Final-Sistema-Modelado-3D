"""Session tokens for the tests, signed with the test JWT_SECRET set in conftest.py."""

import time
from types import SimpleNamespace

from app.config import get_settings
from app.routers.auth import create_access_token, encode_token


def make_token(user_id):
    """A valid token for `user_id`, exactly as POST /auth/login would issue it."""
    token, _ = create_access_token(SimpleNamespace(id=user_id, email=f"{user_id}@test.local"))
    return token


def auth_headers(user_id):
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def make_custom_token(payload=None, secret=None, **claims):
    """Token with arbitrary claims (e.g. an expired exp or no sub) for the negative tests."""
    now = int(time.time())
    data = {"sub": "user-a", "iat": now, "exp": now + 3600} if payload is None else dict(payload)
    data.update(claims)
    return encode_token(data, secret or get_settings().jwt_secret)
