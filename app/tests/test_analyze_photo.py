import base64
import json
import logging

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.space import Habitacion, MuebleSugerido
from app.routers.spaces import MAX_PHOTO_BYTES
from app.services import photo_estimator
from app.services.omniroute import OmniRouteError
from app.tests.test_photo_estimator import ESTIMATE, FakeModel
from app.tests.test_rooms_api import ROOMS, created_id
from app.utils import rate_limit

pytestmark = pytest.mark.anyio

CONFLICT = (
    "Esta sala ya tiene forma o muebles guardados. "
    "Confirma que quieres reemplazarlos por lo que estime la foto."
)
BAD_ESTIMATE = "El servicio de IA no devolvió una estimación válida."
TOO_SLOW = (
    "El análisis de la foto tardó demasiado. Inténtalo de nuevo en un momento o con una foto más pequeña."
)
MODEL_TEXT = "SALIDA-PRIVADA-DEL-MODELO"


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
    """Sustituye al generador de /analyze (lo usa test_rate_limit)."""

    def __init__(self, muebles=None, error=None):
        self.muebles = muebles or []
        self.error = error
        self.calls = []

    async def __call__(self, dims, estilo, instrucciones, superficies, contexto_visual=None, planta=None):
        self.calls.append({"dims": dims, "estilo": estilo, "instrucciones": instrucciones})
        if self.error:
            raise self.error
        return {"descripcion": f"Propuesta {estilo}", "muebles": self.muebles}


def estimate_with(*objects, room=None):
    return json.dumps({**ESTIMATE, "room": room or ESTIMATE["room"], "objects": list(objects)})


def wall_frame(name="Cuadro"):
    return {
        "name": name,
        "kind": "wall",
        "width_cm": 80,
        "depth_cm": 5,
        "height_cm": 60,
        "x_m": 2,
        "z_m": 0.025,
        "elevation_m": 1.4,
    }


@pytest.fixture
def model(monkeypatch):
    """El modelo con visión, sustituido: el estimador y la validación reales sí se ejecutan."""
    fake = FakeModel(json.dumps(ESTIMATE))
    monkeypatch.setattr(photo_estimator, "chat_completion", fake)
    return fake


async def upload(client, room_id, data=None, content_type="image/jpeg", confirm=True, **form):
    if confirm:
        form.setdefault("confirm_replace", "true")
    files = {"file": ("sala.jpg", photo() if data is None else data, content_type)}
    return await client.post(f"{ROOMS}/{room_id}/analyze-photo", files=files, data=form)


async def stored(db_sessions, room_id):
    async with db_sessions() as session:
        query = (
            select(MuebleSugerido).where(MuebleSugerido.habitacion_id == room_id).order_by(MuebleSugerido.id)
        )
        return (await session.scalars(query)).all()


async def stored_room(db_sessions, room_id):
    async with db_sessions() as session:
        return await session.get(Habitacion, room_id)


async def bare_room(db_sessions, with_furniture=False):
    """Sala sin vértices (como las de /analyze), opcionalmente con un mueble."""
    async with db_sessions() as session:
        row = Habitacion(user_id="user-a", nombre="Escaneada", ancho=4, alto=3, profundidad=5)
        if with_furniture:
            row.muebles = [MuebleSugerido(**piece("Mesa", 1, 1))]
        session.add(row)
        await session.commit()
        return row.id


# --- Caso feliz -------------------------------------------------------------------------------------


async def test_the_photo_replaces_the_room_shape_and_its_furniture(user_a, db_sessions, model):
    room_id = await created_id(user_a, vertices=[(0, 0), (5, 0), (5, 5), (0, 5)])
    model.reply = estimate_with(ESTIMATE["objects"][0], wall_frame())

    response = await upload(user_a, room_id, instrucciones="  Solo muebles grandes  ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"room", "estimated", "description", "items", "discarded"}
    assert body["estimated"] is True
    assert body["description"] == "Sala pequeña."
    room = body["room"]
    assert room["id"] == room_id
    assert room["vertices"] == [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}]
    assert (room["heightM"], room["areaM2"]) == (2.5, 12)
    sofa, cuadro = body["items"]
    # Centro (1.5, 1) con huella 2 x 0.9 -> esquina mínima (0.5, 0.55).
    assert (sofa["name"], sofa["x"], sofa["y"], sofa["rotation"]) == ("Sofá", 0.5, pytest.approx(0.55), 0)
    sizes = (sofa["widthM"], sofa["depthM"], sofa["heightM"], sofa["elevationM"])
    assert sizes == pytest.approx((2.0, 0.9, 0.85, 0))
    assert cuadro["elevationM"] == pytest.approx(1.4)
    assert body["discarded"] == []

    (messages,) = model.calls
    text, image = messages[1]["content"]
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert '"instrucciones": "Solo muebles grandes"' in text["text"]


async def test_the_estimate_is_stored_in_the_existing_columns(user_a, db_sessions, model):
    room_id = await created_id(user_a, vertices=[(0, 0), (5, 0), (5, 5), (0, 5)])
    await upload(user_a, room_id)
    model.reply = estimate_with(wall_frame())

    await upload(user_a, room_id)

    row = await stored_room(db_sessions, room_id)
    assert (row.ancho, row.profundidad, row.alto) == (4, 3, 2.5)
    assert row.vertices == [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}]
    (cuadro,) = await stored(db_sessions, room_id)  # el sofá de la primera foto ya no está
    assert cuadro.nombre == "Cuadro"
    assert (cuadro.ancho, cuadro.profundidad, cuadro.alto) == pytest.approx((0.8, 0.05, 0.6))
    assert (cuadro.posicion_x, cuadro.posicion_y, cuadro.posicion_z) == pytest.approx((2, 1.4, 0.025))


async def test_items_lists_the_boxes_after_a_reload(user_a, db_sessions, model):
    room_id = await created_id(user_a)
    await upload(user_a, room_id)

    (item,) = (await user_a.get(f"{ROOMS}/{room_id}/items")).json()["items"]

    assert (item["name"], item["widthM"], item["depthM"], item["heightM"]) == ("Sofá", 2.0, 0.9, 0.85)


async def test_png_is_accepted(user_a, db_sessions, model):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, data=photo(".png"), content_type="image/png")

    assert response.status_code == 200, response.text
    assert model.calls[0][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_a_chunked_upload_under_the_limit_reaches_the_endpoint(user_a, db_sessions, model):
    room_id = await bare_room(db_sessions)
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


# --- Confirmación del reemplazo (409) ----------------------------------------------------------------


async def test_a_room_with_a_shape_needs_confirmation(user_a, db_sessions, model):
    room_id = await created_id(user_a)

    for form in ({}, {"confirm_replace": "false"}):
        response = await upload(user_a, room_id, confirm=False, **form)
        assert response.status_code == 409
        assert response.json()["detail"] == CONFLICT
    assert model.calls == []


async def test_a_room_with_furniture_needs_confirmation(user_a, db_sessions, model):
    room_id = await bare_room(db_sessions, with_furniture=True)

    response = await upload(user_a, room_id, confirm=False)

    assert response.status_code == 409
    assert [p.nombre for p in await stored(db_sessions, room_id)] == ["Mesa"]


async def test_an_empty_room_without_a_shape_needs_no_confirmation(user_a, db_sessions, model):
    room_id = await bare_room(db_sessions)

    response = await upload(user_a, room_id, confirm=False)

    assert response.status_code == 200, response.text


# --- Formulario inválido (422 sin eco) -----------------------------------------------------------------


async def test_instructions_up_to_500_characters_are_accepted(user_a, db_sessions, model):
    room_id = await created_id(user_a)

    assert (await upload(user_a, room_id, instrucciones="a" * 500)).status_code == 200


@pytest.mark.parametrize(
    "form",
    [
        {"instrucciones": "ECO-PRIVADO" + "a" * 490},
        {"confirm_replace": "ECO-PRIVADO"},
    ],
)
async def test_an_invalid_form_is_422_without_echoing_the_input(user_a, db_sessions, model, form):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, **form)

    assert response.status_code == 422
    assert "ECO-PRIVADO" not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}
    assert model.calls == []


async def test_blank_instructions_are_not_sent(user_a, db_sessions, model):
    room_id = await created_id(user_a)

    await upload(user_a, room_id, instrucciones="   ")

    assert "<<<INSTRUCCIONES" not in model.calls[0][1]["content"][0]["text"]


async def test_missing_file_is_422(user_a, db_sessions, model):
    room_id = await created_id(user_a)

    response = await user_a.post(f"{ROOMS}/{room_id}/analyze-photo", data={"confirm_replace": "true"})

    assert response.status_code == 422


# --- Respuestas de la IA: 502, 504 y descartes -------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        MODEL_TEXT,
        estimate_with(room={"width_m": 30, "length_m": 3, "height_m": 2.5}),
        json.dumps({**ESTIMATE, "extra": MODEL_TEXT}),
        '{"room": {"width_m": NaN, "length_m": 3, "height_m": 2.5}, "objects": []}',
    ],
)
async def test_an_unusable_estimate_is_502_and_keeps_the_room(user_a, db_sessions, model, reply):
    room_id = await created_id(user_a)
    await upload(user_a, room_id)
    model.reply = reply

    response = await upload(user_a, room_id)

    assert response.status_code == 502
    assert response.json()["detail"] == BAD_ESTIMATE
    assert MODEL_TEXT not in response.text
    assert [p.nombre for p in await stored(db_sessions, room_id)] == ["Sofá"]


async def test_a_provider_failure_is_502_without_its_text(user_a, db_sessions, model):
    room_id = await created_id(user_a)
    model.error = OmniRouteError(f"request failed: {MODEL_TEXT}")

    response = await upload(user_a, room_id)

    assert response.status_code == 502
    assert response.json()["detail"] == BAD_ESTIMATE


async def test_a_slow_model_is_504_and_keeps_the_room(user_a, db_sessions, model, monkeypatch):
    room_id = await created_id(user_a, vertices=[(0, 0), (5, 0), (5, 5), (0, 5)])
    await upload(user_a, room_id)
    monkeypatch.setattr(get_settings(), "ai_photo_timeout", 0.05)
    model.delay = 5

    response = await upload(user_a, room_id)

    assert response.status_code == 504
    assert response.json()["detail"] == TOO_SLOW
    assert [p.nombre for p in await stored(db_sessions, room_id)] == ["Sofá"]
    assert (await stored_room(db_sessions, room_id)).ancho == 4  # la forma de la primera foto sigue


async def test_invalid_and_extra_objects_are_discarded_with_a_reason(user_a, db_sessions, model):
    room_id = await created_id(user_a)
    many = [{**wall_frame(f"Cuadro {i}"), "x_m": 0.5 + i * 0.25} for i in range(11)]
    outside = {**wall_frame("Fuera"), "x_m": 5}
    model.reply = estimate_with({**wall_frame(), "name": "<b>x</b>"}, outside, *many)

    body = (await upload(user_a, room_id)).json()

    assert len(body["items"]) == 10
    assert body["discarded"] == [
        {"name": "(sin nombre)", "reason": "datos fuera de los límites"},
        {"name": "Fuera", "reason": "fuera de la habitación"},
        {"name": "Cuadro 10", "reason": "límite de 10 objetos"},
    ]


# --- Cuota: una foto cuesta 3 llamadas ------------------------------------------------------------------


async def test_a_photo_uses_3_calls_of_the_ai_quota(user_a, db_sessions, model, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", 10)
    rate_limit.reset_all()
    room_id = await created_id(user_a)
    for _ in range(3):
        assert (await upload(user_a, room_id)).status_code == 200

    response = await upload(user_a, room_id)

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert len(model.calls) == 3


async def test_requests_refused_before_the_model_use_no_quota(user_a, db_sessions, model, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", 3)
    rate_limit.reset_all()
    room_id = await created_id(user_a)
    await upload(user_a, 999)  # 404
    await upload(user_a, room_id, confirm=False)  # 409
    await upload(user_a, room_id, content_type="image/gif")  # 415
    await upload(user_a, room_id, data=b"\x89PNG\r\n\x1a\ngarbage", content_type="image/png")  # 422

    assert (await upload(user_a, room_id)).status_code == 200


# --- Nada sensible en logs ni en respuestas --------------------------------------------------------------


async def test_neither_photo_instructions_nor_model_output_reach_the_logs(user_a, db_sessions, model, caplog):
    caplog.set_level(logging.DEBUG)
    room_id = await created_id(user_a)
    image = photo()
    model.reply = json.dumps({**ESTIMATE, "description": MODEL_TEXT})
    await upload(user_a, room_id, data=image, instrucciones="INSTRUCCION-PRIVADA")
    model.reply = MODEL_TEXT
    await upload(user_a, room_id, data=image, instrucciones="INSTRUCCION-PRIVADA")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    encoded = base64.b64encode(image).decode()
    for private in (encoded[:40], MODEL_TEXT, "INSTRUCCION-PRIVADA"):
        assert private not in logged


# --- Rejected uploads ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type", ["image/gif", "image/webp", "text/plain", "application/octet-stream"]
)
async def test_only_jpeg_and_png_are_accepted(user_a, db_sessions, model, content_type):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, content_type=content_type)

    assert response.status_code == 415
    assert model.calls == []


JPEG_START = b"\xff\xd8\xff"
PNG_START = b"\x89PNG\r\n\x1a\n"


async def test_photo_larger_than_5_mb_is_413(user_a, db_sessions, model):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, data=JPEG_START + b"\x00" * MAX_PHOTO_BYTES)

    assert response.status_code == 413
    assert model.calls == []


async def test_photo_of_exactly_5_mb_passes_the_size_check(user_a, db_sessions, model):
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
async def test_first_bytes_must_match_the_declared_type(user_a, db_sessions, model, data, content_type):
    room_id = await created_id(user_a)

    response = await upload(user_a, room_id, data=data, content_type=content_type)

    assert response.status_code == 415
    assert model.calls == []


async def test_undecodable_image_is_422_with_a_fixed_text(user_a, db_sessions, model):
    room_id = await created_id(user_a)

    # Right signature, broken content.
    response = await upload(user_a, room_id, data=PNG_START + b"garbage", content_type="image/png")

    assert response.status_code == 422
    assert response.json()["detail"] == "Imagen inválida: no se pudo leer la foto."
    assert model.calls == []


async def test_huge_declared_length_is_413_before_parsing_or_authenticating(client, db_sessions, model):
    # No token and an absurd Content-Length: refused by the middleware before reading the body.
    response = await client.post(
        f"{ROOMS}/1/analyze-photo",
        content=b"x",
        headers={"Content-Type": "multipart/form-data; boundary=b", "Content-Length": str(50 * 1024 * 1024)},
    )

    assert response.status_code == 413


async def test_chunked_body_over_the_limit_is_413_before_parsing_or_authenticating(
    client, db_sessions, model
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


# --- Authentication and ownership ------------------------------------------------------------------


async def test_photo_needs_a_token(client, db_sessions, model):
    response = await upload(client, 1)

    assert response.status_code == 401
    assert model.calls == []


async def test_room_of_another_account_is_404(user_a, user_b, db_sessions, model):
    room_id = await created_id(user_a)

    response = await upload(user_b, room_id)

    assert response.status_code == 404
    assert model.calls == []


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
