import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app.main import app
from app.models.space import MuebleSugerido
from app.routers.spaces import MAX_PHOTO_BYTES, get_layout_suggester
from app.services.omniroute import OmniRouteError
from app.tests.test_room import NOTCHED_ROOM
from app.tests.test_rooms_api import ROOMS, created_id

pytestmark = pytest.mark.anyio


def photo(fmt=".jpg"):
    """A small room photo: warm wall on top, grey floor below."""
    image = np.zeros((60, 80, 3), dtype=np.uint8)
    image[:30] = (60, 140, 220)  # BGR
    image[30:] = (128, 128, 128)
    ok, encoded = cv2.imencode(fmt, image)
    assert ok
    return encoded.tobytes()


def piece(nombre, x, z, ancho=1.0, profundidad=0.6, alto=0.8):
    return {
        "nombre": nombre,
        "ancho": ancho,
        "alto": alto,
        "profundidad": profundidad,
        "posicion_x": x,
        "posicion_y": 0,
        "posicion_z": z,
    }


class FakeSuggester:
    def __init__(self, muebles=None, error=None):
        self.muebles = muebles or []
        self.error = error
        self.calls = []

    async def __call__(self, dims, estilo, instrucciones, superficies, contexto_visual=None, planta=None):
        self.calls.append(
            {
                "dims": dims,
                "estilo": estilo,
                "instrucciones": instrucciones,
                "visual": contexto_visual,
                "planta": planta,
            }
        )
        if self.error:
            raise self.error
        return {"descripcion": f"Propuesta {estilo}", "muebles": self.muebles}


@pytest.fixture
def suggester():
    fake = FakeSuggester()
    app.dependency_overrides[get_layout_suggester] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_layout_suggester, None)


async def upload(client, room_id, data=None, content_type="image/jpeg", **form):
    files = {"file": ("sala.jpg", photo() if data is None else data, content_type)}
    return await client.post(f"{ROOMS}/{room_id}/analyze-photo", files=files, data=form)


async def stored(db_sessions, room_id):
    async with db_sessions() as session:
        query = (
            select(MuebleSugerido).where(MuebleSugerido.habitacion_id == room_id).order_by(MuebleSugerido.id)
        )
        return (await session.scalars(query)).all()


async def test_photo_furnishes_the_saved_room(user_a, db_sessions, suggester):
    room_id = await created_id(user_a, vertices=[(0, 0), (4, 0), (4, 5), (0, 5)])
    suggester.muebles = [piece("Mesa", 1.5, 1.3), piece("Armario", 3.8, 1, ancho=1)]

    response = await upload(user_a, room_id, estilo="nórdico", instrucciones="Poco mobiliario")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"room", "description", "items", "discarded", "visualContext"}
    assert body["room"]["id"] == room_id
    assert body["description"] == "Propuesta nórdico"
    (mesa,) = body["items"]
    assert mesa["name"] == "Mesa"
    # Center (1.5, 1.3) with a 1 x 0.6 footprint -> minimum corner (1.0, 1.0).
    assert (mesa["x"], mesa["y"], mesa["rotation"]) == pytest.approx((1.0, 1.0, 0))
    assert body["discarded"] == [{"name": "Armario", "reason": "colisión"}]
    assert 0 <= body["visualContext"]["brightness"] <= 1
    assert body["visualContext"]["colors"][0].keys() == {"hex", "share"}

    (call,) = suggester.calls
    assert call["dims"] == (4, 2.6, 5)
    assert (call["estilo"], call["instrucciones"]) == ("nórdico", "Poco mobiliario")
    assert set(call["visual"]) == {"brillo", "colores"}


async def test_photo_furniture_is_stored_as_centers_and_replaces_the_previous(user_a, db_sessions, suggester):
    room_id = await created_id(user_a, vertices=[(0, 0), (4, 0), (4, 5), (0, 5)])
    suggester.muebles = [piece("Sofá", 2, 1)]
    await upload(user_a, room_id)
    suggester.muebles = [piece("Cama", 2, 3, ancho=1.4, profundidad=2)]

    await upload(user_a, room_id)

    (cama,) = await stored(db_sessions, room_id)
    assert cama.nombre == "Cama"
    assert (cama.posicion_x, cama.posicion_y, cama.posicion_z) == (2, 0, 3)


async def test_pieces_outside_the_real_polygon_are_discarded(user_a, db_sessions, suggester):
    room_id = await created_id(user_a, vertices=NOTCHED_ROOM, height_m=2.5)
    suggester.muebles = [
        piece("En la muesca", 3, 0.7),
        piece("Cruza la muesca", 3, 0.5, ancho=3, profundidad=0.4),
        piece("Bien", 1, 2.5),
    ]

    body = (await upload(user_a, room_id)).json()

    assert [i["name"] for i in body["items"]] == ["Bien"]
    assert body["discarded"] == [
        {"name": "En la muesca", "reason": "fuera de la habitación"},
        {"name": "Cruza la muesca", "reason": "fuera de la habitación"},
    ]
    # Irregular rooms send their floor polygon to the AI.
    assert len(suggester.calls[0]["planta"]) == 21


async def test_a_room_away_from_the_origin_is_shifted_for_the_ai_and_back(user_a, db_sessions, suggester):
    room_id = await created_id(user_a, vertices=[(10, -2), (14, -2), (14, 3), (10, 3)])
    suggester.muebles = [piece("Mesa", 1.5, 1.3)]

    body = (await upload(user_a, room_id)).json()

    call = suggester.calls[0]
    assert call["dims"] == (4, 2.6, 5)
    assert call["planta"][0] == {"x": 0, "y": 0}
    assert (body["items"][0]["x"], body["items"][0]["y"]) == pytest.approx((11.0, -1.0))
    (mesa,) = await stored(db_sessions, room_id)
    assert (mesa.posicion_x, mesa.posicion_z) == pytest.approx((11.5, -0.7))


async def test_a_rectangular_room_from_analyze_sends_no_polygon(user_a, db_sessions, suggester):
    from app.models.space import Habitacion

    async with db_sessions() as session:
        row = Habitacion(user_id="user-a", nombre="Escaneada", ancho=4, alto=3, profundidad=5)
        session.add(row)
        await session.commit()

    response = await upload(user_a, row.id)

    assert response.status_code == 200
    assert suggester.calls[0]["planta"] is None


async def test_png_is_accepted(user_a, db_sessions, suggester):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, data=photo(".png"), content_type="image/png")

    assert response.status_code == 200, response.text


# --- Rejected uploads ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type", ["image/gif", "image/webp", "text/plain", "application/octet-stream"]
)
async def test_only_jpeg_and_png_are_accepted(user_a, db_sessions, suggester, content_type):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, content_type=content_type)

    assert response.status_code == 415
    assert suggester.calls == []


JPEG_START = b"\xff\xd8\xff"
PNG_START = b"\x89PNG\r\n\x1a\n"


async def test_photo_larger_than_5_mb_is_413(user_a, db_sessions, suggester):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, data=JPEG_START + b"\x00" * MAX_PHOTO_BYTES)

    assert response.status_code == 413
    assert suggester.calls == []


async def test_photo_of_exactly_5_mb_passes_the_size_check(user_a, db_sessions, suggester):
    room_id = await created_id(user_a)

    # Starts like a JPEG but is not one, so it fails later, at decoding (422), not at the size check.
    response = await upload(user_a, room_id, data=JPEG_START + b"\x00" * (MAX_PHOTO_BYTES - 3))

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"this is not a picture", "image/png"),
        (b"GIF89a" + b"\x00" * 20, "image/jpeg"),
        (JPEG_START + b"\x00" * 20, "image/png"),  # a JPEG declared as PNG
        (PNG_START + b"\x00" * 20, "image/jpeg"),  # a PNG declared as JPEG
    ],
)
async def test_first_bytes_must_match_the_declared_type(user_a, db_sessions, suggester, data, content_type):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, data=data, content_type=content_type)

    assert response.status_code == 415
    assert suggester.calls == []


async def test_undecodable_image_is_422(user_a, db_sessions, suggester):
    room_id = await created_id(user_a)

    # Right signature, broken content.
    response = await upload(user_a, room_id, data=PNG_START + b"garbage", content_type="image/png")

    assert response.status_code == 422
    assert "Imagen inválida" in response.json()["detail"]
    assert suggester.calls == []


async def test_huge_declared_length_is_413_before_parsing_or_authenticating(client, db_sessions, suggester):
    # No token and an absurd Content-Length: refused by the middleware before reading the body.
    response = await client.post(
        f"{ROOMS}/1/analyze-photo",
        content=b"x",
        headers={"Content-Type": "multipart/form-data; boundary=b", "Content-Length": str(50 * 1024 * 1024)},
    )

    assert response.status_code == 413


async def test_chunked_body_over_the_limit_is_413_before_parsing_or_authenticating(
    client, db_sessions, suggester
):
    async def chunks():
        for _ in range(7):
            yield b"\x00" * (1024 * 1024)

    # A generator body is sent with Transfer-Encoding: chunked and no Content-Length.
    response = await client.post(
        f"{ROOMS}/1/analyze-photo",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )

    assert response.status_code == 413


async def test_chunked_upload_under_the_limit_reaches_the_endpoint(user_a, db_sessions, suggester):
    room_id = await created_id(user_a)
    boundary = "photo-boundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sala.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + photo(".png")
        + f"\r\n--{boundary}--\r\n".encode()
    )

    async def chunks():
        for i in range(0, len(body), 100):
            yield body[i : i + 100]

    response = await user_a.post(
        f"{ROOMS}/{room_id}/analyze-photo",
        content=chunks(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 200, response.text


async def test_missing_file_is_422(user_a, db_sessions, suggester):
    room_id = await created_id(user_a)

    response = await user_a.post(f"{ROOMS}/{room_id}/analyze-photo", data={"estilo": "moderno"})

    assert response.status_code == 422


async def test_omniroute_failure_is_502_and_keeps_the_furniture(user_a, db_sessions, suggester):
    room_id = await created_id(user_a)
    suggester.muebles = [piece("Mesa", 1.5, 1.3)]
    await upload(user_a, room_id)
    suggester.error = OmniRouteError("request failed")

    response = await upload(user_a, room_id)

    assert response.status_code == 502
    assert response.json()["detail"].startswith("OmniRoute:")
    assert [p.nombre for p in await stored(db_sessions, room_id)] == ["Mesa"]


# --- Authentication and ownership ------------------------------------------------------------------


async def test_photo_needs_a_token(client, db_sessions, suggester):
    response = await upload(client, 1)

    assert response.status_code == 401
    assert suggester.calls == []


async def test_room_of_another_account_is_404(user_a, user_b, db_sessions, suggester):
    room_id = await created_id(user_a)

    response = await upload(user_b, room_id)

    assert response.status_code == 404
    assert suggester.calls == []


# --- BodySizeLimitMiddleware on its own ---------------------------------------------------------------


async def run_middleware(messages, path="/upload", headers=()):
    from app.utils.middlewares import BodySizeLimitMiddleware

    seen = []

    async def inner_app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        seen.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    pending = list(messages)

    async def receive():
        return pending.pop(0)

    sent = []

    async def send(message):
        sent.append(message)

    middleware = BodySizeLimitMiddleware(inner_app, max_bytes=10, path_pattern=r"^/upload$")
    scope = {"type": "http", "method": "POST", "path": path, "headers": list(headers)}
    await middleware(scope, receive, send)
    return seen, sent[0]["status"], len(pending)


async def test_middleware_passes_a_body_under_the_limit_unchanged():
    seen, status, _ = await run_middleware(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"678"},
        ]
    )

    assert (seen, status) == ([b"12345678"], 200)


async def test_middleware_stops_reading_as_soon_as_the_limit_is_passed():
    messages = [{"type": "http.request", "body": b"123456", "more_body": True} for _ in range(5)]

    seen, status, unread = await run_middleware(messages)

    # The application never ran, and the last three chunks were never read.
    assert (seen, status, unread) == ([], 413, 3)


async def test_middleware_trusts_a_large_content_length_without_reading():
    seen, status, unread = await run_middleware(
        [{"type": "http.request", "body": b"1"}], headers=[(b"content-length", b"11")]
    )

    assert (seen, status, unread) == ([], 413, 1)


async def test_middleware_ignores_other_paths():
    seen, status, _ = await run_middleware([{"type": "http.request", "body": b"x" * 50}], path="/other")

    assert (seen, status) == ([b"x" * 50], 200)
