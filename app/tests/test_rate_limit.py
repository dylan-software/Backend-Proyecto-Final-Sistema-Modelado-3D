import asyncio

import pytest

from app.config import Settings, get_settings
from app.main import app
from app.routers.chat import get_room_assistant
from app.routers.spaces import get_coherence_validator, get_layout_suggester
from app.tests.test_analyze_photo import FakeSuggester, upload
from app.tests.test_api import catalog_item, room_scan
from app.tests.test_auth import REGISTER
from app.tests.test_chat import ask
from app.tests.test_rooms_api import created_id
from app.utils import rate_limit
from app.utils.rate_limit import DAY, MINUTE, RateLimiter

pytestmark = pytest.mark.anyio

CHAT = "/api/v1/chat"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def chat_model():
    """Stands in for the chat model; records every conversation it receives."""
    calls = []

    async def assistant(context, conversation):
        calls.append(conversation)
        return "De acuerdo."

    app.dependency_overrides[get_room_assistant] = lambda: assistant
    yield calls
    app.dependency_overrides.pop(get_room_assistant, None)


def limit_ai_calls(monkeypatch, per_minute, per_day=200):
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", per_minute)
    monkeypatch.setattr(get_settings(), "ai_requests_per_day", per_day)
    rate_limit.reset_all()


# --- RateLimiter ---------------------------------------------------------------------------------


def test_the_event_after_the_limit_waits_until_the_oldest_leaves_the_window():
    clock = FakeClock()
    limiter = RateLimiter([(3, MINUTE)], clock=clock)
    for _ in range(3):
        assert limiter.retry_after("a") == 0
        limiter.record("a")
        clock.now += 10

    assert limiter.retry_after("a") == pytest.approx(30)  # the first one leaves at +60
    clock.now += 30
    assert limiter.retry_after("a") == 0


def test_keys_are_counted_separately():
    limiter = RateLimiter([(1, MINUTE)], clock=FakeClock())
    limiter.record("a")

    assert limiter.retry_after("a") > 0
    assert limiter.retry_after("b") == 0


def test_every_window_applies_and_the_longest_wait_wins():
    clock = FakeClock()
    limiter = RateLimiter([(10, MINUTE), (12, DAY)], clock=clock)
    for _ in range(12):
        limiter.record("a")
        clock.now += MINUTE  # never more than one per minute

    assert limiter.retry_after("a") == pytest.approx(DAY - 12 * MINUTE)


def test_reset_forgets_a_key():
    limiter = RateLimiter([(1, MINUTE)], clock=FakeClock())
    limiter.record("a")
    limiter.reset("a")

    assert limiter.retry_after("a") == 0


def test_idle_keys_are_dropped_when_too_many_are_kept(monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_KEYS", 3)
    clock = FakeClock()
    limiter = RateLimiter([(1, MINUTE)], clock=clock)
    for key in "abc":
        limiter.record(key)
    clock.now += 2 * MINUTE
    limiter.record("d")

    assert list(limiter._events) == ["d"]


def test_ai_limits_default_to_10_per_minute_and_200_per_day():
    assert Settings.model_fields["ai_requests_per_minute"].default == 10
    assert Settings.model_fields["ai_requests_per_day"].default == 200


# --- AI endpoints, per account ----------------------------------------------------------------------


async def test_the_11th_chat_message_in_a_minute_is_429_and_other_accounts_are_not_affected(
    user_a,
    user_b,
    db_sessions,
    chat_model,
):
    room_a = await created_id(user_a)
    room_b = await created_id(user_b)
    for _ in range(10):
        assert (await user_a.post(CHAT, json={"roomId": room_a, "messages": ask()})).status_code == 200

    response = await user_a.post(CHAT, json={"roomId": room_a, "messages": ask()})

    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    assert "demasiadas peticiones a la IA" in response.json()["detail"]
    assert (await user_b.post(CHAT, json={"roomId": room_b, "messages": ask()})).status_code == 200


async def test_requests_rejected_before_the_model_do_not_use_the_quota(
    user_a,
    db_sessions,
    chat_model,
    monkeypatch,
):
    limit_ai_calls(monkeypatch, per_minute=1)
    room_id = await created_id(user_a)
    for _ in range(3):
        # Room of nobody: 404 before the model is called.
        assert (await user_a.post(CHAT, json={"roomId": 999, "messages": ask()})).status_code == 404

    assert (await user_a.post(CHAT, json={"roomId": room_id, "messages": ask()})).status_code == 200
    assert (await user_a.post(CHAT, json={"roomId": room_id, "messages": ask()})).status_code == 429


async def test_the_daily_limit_applies_too(user_a, db_sessions, chat_model, monkeypatch):
    limit_ai_calls(monkeypatch, per_minute=10, per_day=2)
    room_id = await created_id(user_a)
    for _ in range(2):
        assert (await user_a.post(CHAT, json={"roomId": room_id, "messages": ask()})).status_code == 200

    response = await user_a.post(CHAT, json={"roomId": room_id, "messages": ask()})

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 60


async def test_catalog_shares_the_ai_quota_and_answers_429(user_a, user_b, db_sessions, monkeypatch):
    limit_ai_calls(monkeypatch, per_minute=2)
    calls = []

    async def validator(nombre):
        calls.append(nombre)
        return True

    app.dependency_overrides[get_coherence_validator] = lambda: validator
    try:
        for _ in range(2):
            assert (
                await user_a.post("/api/v1/spaces/catalog", json=catalog_item("Silla"))
            ).status_code == 201
        response = await user_a.post("/api/v1/spaces/catalog", json=catalog_item("Silla"))
        other = await user_b.post("/api/v1/spaces/catalog", json=catalog_item("Silla"))
    finally:
        app.dependency_overrides.pop(get_coherence_validator, None)

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert len(calls) == 3  # two of user-a and the one of user-b: the 429 never reached the model
    assert other.status_code == 201


async def test_analyze_and_analyze_photo_answer_429_beyond_the_quota(
    user_a, user_b, db_sessions, monkeypatch
):
    limit_ai_calls(monkeypatch, per_minute=2)
    fake = FakeSuggester()
    app.dependency_overrides[get_layout_suggester] = lambda: fake
    try:
        room_id = await created_id(user_a)
        analyze = await user_a.post(
            "/api/v1/spaces/analyze", json={"nombre": "Sala", "puntos": room_scan().tolist()}
        )
        photo = await upload(user_a, room_id)
        over = await upload(user_a, room_id)
        other = await upload(user_b, await created_id(user_b))
    finally:
        app.dependency_overrides.pop(get_layout_suggester, None)

    assert (analyze.status_code, photo.status_code, over.status_code) == (201, 200, 429)
    assert "Retry-After" in over.headers
    assert len(fake.calls) == 3
    assert other.status_code == 200


# --- Failed logins, per email -------------------------------------------------------------------------

LOGIN = "/api/v1/auth/login"
RIGHT = {"email": "ana@mail.com", "password": "contraseña-larga"}
WRONG = {"email": "ana@mail.com", "password": "otra-contraseña"}


def test_login_failures_default_to_10_in_15_minutes():
    assert Settings.model_fields["login_failures_per_email"].default == 10
    assert Settings.model_fields["login_failure_window_minutes"].default == 15


async def test_the_11th_login_after_10_failures_is_429_even_with_the_right_password(client, db_sessions):
    await client.post("/api/v1/auth/register", json=REGISTER)
    await client.post("/api/v1/auth/register", json={**REGISTER, "email": "beto@mail.com"})
    for _ in range(10):
        assert (await client.post(LOGIN, json=WRONG)).status_code == 401

    response = await client.post(LOGIN, json=RIGHT)

    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 15 * 60
    assert response.json()["detail"] == (
        "Demasiados intentos fallidos para este email; espera antes de volver a intentarlo."
    )
    # Another email is not affected.
    assert (await client.post(LOGIN, json={**RIGHT, "email": "beto@mail.com"})).status_code == 200


async def test_the_login_limit_ignores_forged_forwarding_headers_and_email_case(client, db_sessions):
    await client.post("/api/v1/auth/register", json=REGISTER)
    for i in range(10):
        forged = {"X-Forwarded-For": f"10.0.0.{i}"}
        assert (await client.post(LOGIN, json=WRONG, headers=forged)).status_code == 401

    response = await client.post(
        LOGIN, json={**RIGHT, "email": " ANA@Mail.com "}, headers={"X-Forwarded-For": "10.9.9.9"}
    )

    assert response.status_code == 429


async def test_failures_for_an_unknown_email_are_counted_too(client, db_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_failures_per_email", 2)
    rate_limit.reset_all()
    nobody = {"email": "nadie@mail.com", "password": "contraseña-larga"}
    for _ in range(2):
        assert (await client.post(LOGIN, json=nobody)).status_code == 401

    assert (await client.post(LOGIN, json=nobody)).status_code == 429


async def test_a_successful_login_clears_the_failures(client, db_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_failures_per_email", 2)
    rate_limit.reset_all()
    await client.post("/api/v1/auth/register", json=REGISTER)
    assert (await client.post(LOGIN, json=WRONG)).status_code == 401
    assert (await client.post(LOGIN, json=RIGHT)).status_code == 200

    assert (await client.post(LOGIN, json=WRONG)).status_code == 401
    assert (await client.post(LOGIN, json=WRONG)).status_code == 401
    assert (await client.post(LOGIN, json=RIGHT)).status_code == 429


async def test_simultaneous_wrong_logins_cannot_get_past_the_limit(client, db_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_failures_per_email", 3)
    rate_limit.reset_all()
    await client.post("/api/v1/auth/register", json=REGISTER)

    responses = await asyncio.gather(*[client.post(LOGIN, json=WRONG) for _ in range(8)])

    assert sorted(r.status_code for r in responses) == [401] * 3 + [429] * 5
