"""Foto a 3D de extremo a extremo (sin IA real): respuesta del modelo -> plan -> guardado -> GET /items.

Comprueba con una geometría propia (no la de app.geometry.arrangement) que las cajas que recibe el
frontend, en el marco de la API (esquina mínima de la huella + medidas, en metros), no se intersecan y
quedan dentro de la sala. Tocarse (apoyado encima o pegado al lado) no cuenta como choque.
"""

import json
import random

import pytest

from app.tests.test_analyze_photo import model, upload  # noqa: F401  (fixture y ayuda reutilizadas)
from app.tests.test_photo_estimator import ESTIMATE
from app.tests.test_rooms_api import ROOMS, created_id

pytestmark = pytest.mark.anyio

# Holgura numérica: lo que solapa menos de 1 micra se considera que se toca.
TOUCH_M = 1e-6

# Sala y objetos como los de la captura del usuario, todos amontonados alrededor de la cama.
CAPTURE_ROOM = {"width_m": 3.5, "length_m": 3.2, "height_m": 2.5}
def estimated(name, kind, size_cm, center_m, elevation_m=0.0):
    """Objeto como lo devuelve el modelo: medidas (ancho, fondo, alto) en cm y centro (x, z) en m."""
    (width, depth, height), (x, z) = size_cm, center_m
    return {
        "name": name,
        "kind": kind,
        "width_cm": width,
        "depth_cm": depth,
        "height_cm": height,
        "x_m": x,
        "z_m": z,
        "elevation_m": elevation_m,
    }


CAPTURE_OBJECTS = [
    estimated("Cama", "floor", (140, 190, 50), (1.2, 1.5)),
    estimated("Bandeja para portátil", "floor", (50, 30, 25), (1.2, 1.4)),
    estimated("Espejo de tocador", "wall", (50, 5, 120), (1.0, 0.6), elevation_m=0.3),
    estimated("Mochila", "floor", (30, 20, 45), (1.9, 2.0)),
    estimated("Pantuflas", "floor", (30, 25, 10), (0.9, 2.5)),
    estimated("Puerta", "wall", (80, 5, 200), (0.3, 0.1)),
]


def box(item):
    """(x0, x1, z0, z1, y0, y1) de un item de GET /items: esquina mínima + medidas, base en elevationM."""
    x0, z0 = item["x"], item["y"]
    y0 = item["elevationM"]
    return x0, x0 + item["widthM"], z0, z0 + item["depthM"], y0, y0 + item["heightM"]


def intersect(a, b):
    """True si las dos cajas comparten volumen (más de TOUCH_M en los tres ejes)."""
    return all(min(a[i + 1], b[i + 1]) - max(a[i], b[i]) > TOUCH_M for i in (0, 2, 4))


def collisions(items):
    boxes = [(item["name"], box(item)) for item in items]
    return [
        (name_a, name_b)
        for i, (name_a, a) in enumerate(boxes)
        for name_b, b in boxes[i + 1 :]
        if intersect(a, b)
    ]


def outside(items, room):
    limits = (room["width_m"], room["length_m"], room["height_m"])
    bad = []
    for item in items:
        x0, x1, z0, z1, y0, y1 = box(item)
        lows, highs = (x0, z0, y0), (x1, z1, y1)
        too_low = any(low < -TOUCH_M for low in lows)
        too_high = any(high > limit + TOUCH_M for high, limit in zip(highs, limits, strict=True))
        if too_low or too_high:
            bad.append(item["name"])
    return bad


async def analyze_and_list(client, model_fake, room, objects):
    """Sube una foto con la respuesta del modelo dada y devuelve (respuesta del POST, items de GET /items)."""
    model_fake.reply = json.dumps({**ESTIMATE, "room": room, "objects": objects})
    room_id = await created_id(client)
    response = await upload(client, room_id)
    assert response.status_code == 200, response.text
    listed = (await client.get(f"{ROOMS}/{room_id}/items")).json()["items"]
    return response.json(), listed


async def test_the_capture_scene_reaches_the_frontend_without_collisions(user_a, db_sessions, model):  # noqa: F811
    posted, listed = await analyze_and_list(user_a, model, CAPTURE_ROOM, CAPTURE_OBJECTS)

    # Lo que devuelve el POST y lo que se lee después son lo mismo.
    assert sorted(posted["items"], key=lambda i: i["id"]) == sorted(listed, key=lambda i: i["id"])
    assert collisions(listed) == []
    assert outside(listed, CAPTURE_ROOM) == []
    # Para la prueba del frontend (tests/photoScene.test.ts): las cajas tal como llegan del backend.
    print("CAPTURE_ITEMS", json.dumps(listed, ensure_ascii=False))
    print("CAPTURE_DISCARDED", json.dumps(posted["discarded"], ensure_ascii=False))


async def test_the_wall_furniture_scene_reaches_the_frontend_against_the_walls(user_a, db_sessions, model):  # noqa: F811
    """FASE 4: cama, tocador, armario y puerta (la escena de app/tests/test_arrangement_walls.py)."""
    room = {"width_m": 4.0, "length_m": 3.0, "height_m": 2.5}
    objects = [
        estimated("Cama", "floor", (140, 190, 50), (1.2, 1.1)),
        estimated("Tocador", "floor", (45, 100, 80), (0.35, 2.2)),
        estimated("Armario", "floor", (100, 60, 200), (3.3, 0.32)),
        estimated("Puerta", "wall", (90, 5, 200), (3.45, 0.02)),
    ]

    posted, listed = await analyze_and_list(user_a, model, room, objects)

    assert posted["discarded"] == []
    assert collisions(listed) == []
    assert outside(listed, room) == []
    corners = {item["name"]: (item["x"], item["y"]) for item in listed}
    assert corners["Cama"] == (pytest.approx(0.5), pytest.approx(0.0))  # cabecero contra el fondo
    assert corners["Tocador"] == (pytest.approx(0.0), pytest.approx(1.7))  # contra la pared izquierda
    assert corners["Armario"] == (pytest.approx(1.99), pytest.approx(0.0))  # junto a la puerta, no delante
    assert corners["Puerta"] == (pytest.approx(3.0), pytest.approx(0.0))


def random_scene(rng):
    """Una sala y de 4 a 10 objetos amontonados, como podría devolverlos el modelo."""
    room = {
        "width_m": round(rng.uniform(2.0, 6.0), 2),
        "length_m": round(rng.uniform(2.0, 6.0), 2),
        "height_m": round(rng.uniform(2.2, 3.0), 2),
    }
    objects = []
    for i in range(rng.randint(4, 10)):
        kind = "wall" if rng.random() < 0.3 else "floor"
        name = rng.choice(["Puerta", "Espejo", "Cuadro", "Ventana"]) if kind == "wall" else f"Mueble {i}"
        objects.append(
            {
                "name": f"{name} {i}",
                "kind": kind,
                "width_cm": round(rng.uniform(10, 200)),
                "depth_cm": round(rng.uniform(5, 200)) if kind == "floor" else 5,
                "height_cm": round(rng.uniform(5, 200)),
                # Amontonados en el centro de la sala, para forzar choques.
                "x_m": round(room["width_m"] / 2 + rng.uniform(-0.8, 0.8), 2),
                "z_m": round(room["length_m"] / 2 + rng.uniform(-0.8, 0.8), 2),
                "elevation_m": round(rng.uniform(0, 1.5), 2),
            }
        )
    return room, objects


@pytest.mark.parametrize("seed", range(40))
async def test_random_crowded_scenes_never_collide_once_listed(user_a, db_sessions, model, seed):  # noqa: F811
    room, objects = random_scene(random.Random(seed))
    _, listed = await analyze_and_list(user_a, model, room, objects)

    assert collisions(listed) == []
    assert outside(listed, room) == []
