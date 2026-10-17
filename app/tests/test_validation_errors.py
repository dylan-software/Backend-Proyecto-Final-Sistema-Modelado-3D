import pytest

pytestmark = pytest.mark.anyio

# Not valid UTF-8: 0xff can never start a UTF-8 sequence.
NON_UTF8_BODY = b'{"email": "\xff\xfe", "password": "x"}'


@pytest.mark.parametrize("content_type", ["text/plain", "application/octet-stream"])
async def test_non_utf8_body_is_a_422_not_a_500(client, content_type):
    response = await client.post(
        "/api/v1/auth/login", content=NON_UTF8_BODY, headers={"Content-Type": content_type}
    )

    assert response.status_code == 422, response.text
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}


async def test_non_utf8_body_on_an_authenticated_endpoint_is_a_422(user_a):
    response = await user_a.post(
        "/api/v1/spaces/catalog", content=NON_UTF8_BODY, headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 422, response.text


async def test_validation_errors_do_not_echo_the_input(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"nombre": "Ana", "email": "not-an-email", "password": "secret-password-123"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(error["loc"][-1] == "email" for error in detail)
    assert "not-an-email" not in response.text
    for error in detail:
        assert "input" not in error and "ctx" not in error
