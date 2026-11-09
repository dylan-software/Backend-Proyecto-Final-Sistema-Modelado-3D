"""Parser y validación del archivo de proyecto (CONTRATO-FASE6.md): archivos válidos, malformados, de otra
versión, con campos de más o fuera de los límites. Ningún mensaje repite la entrada."""

import copy
import json
import math

import pytest

from app.schemas.room import MAX_VERTICES
from app.services.project_import import (
    INVALID_POLYGON,
    ITEM_COUNT,
    NOT_A_PROJECT,
    NOT_JSON,
    VERTEX_COUNT,
    WRONG_VERSION,
    ProjectImportError,
    floor_object_raised,
    load_project,
    object_above_ceiling,
    object_outside,
    too_many_parts,
)

MARKER = "robar"
PART = {"shape": "box", "size_cm": [10, 10, 10], "offset_cm": [0, 0, 0], "color": "#A0B1C2"}
BED = {
    "name": "Cama",
    "category": None,
    "kind": "floor",
    "size_cm": [140, 50, 190],
    "position": {"x_m": 1.2, "z_m": 1.5, "elevation_m": 0, "rotation_deg": 0},
    "wall": None,
    "parts": [],
}
PICTURE = {
    "name": "Cuadro",
    "category": "Decoración",
    "kind": "wall",
    "size_cm": [5, 40, 60],
    "position": {"x_m": 0.025, "z_m": 1.5, "elevation_m": 1.3, "rotation_deg": 0},
    "wall": "left",
    "parts": [PART],
}
VALID = {
    "format": "habita-project",
    "version": 1,
    "name": "Prueba 1",
    "room": {"height_m": 2.5, "vertices": [[0, 0], [4, 0], [4, 3], [0, 3]], "estimated": False},
    "items": [BED, PICTURE],
}
L_ROOM = [[0, 0], [4, 0], [4, 2], [2, 2], [2, 4], [0, 4]]  # muesca: x 2-4, z 2-4


def project(**changes):
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def item(base=BED, **changes):
    data = copy.deepcopy(base)
    data.update(changes)
    return data


def raw(data):
    return json.dumps(data).encode()


def rejected(body):
    """El mensaje del rechazo; falla si el archivo se acepta."""
    with pytest.raises(ProjectImportError) as caught:
        load_project(body if isinstance(body, bytes) else raw(body))
    message = caught.value.message
    assert MARKER not in message
    return message


def circle(count, radius=3.0):
    return [
        [
            round(radius * math.cos(2 * math.pi * i / count), 9),
            round(radius * math.sin(2 * math.pi * i / count), 9),
        ]
        for i in range(count)
    ]


# --- Archivos válidos ---------------------------------------------------------------------------------


def test_a_valid_file_becomes_a_room_and_its_pieces():
    plan = load_project(raw(VALID))

    assert plan.name == "Prueba 1"
    assert plan.room["vertices"] == [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}]
    assert (plan.room["ancho"], plan.room["profundidad"], plan.room["alto"]) == (4, 3, 2.5)
    bed, picture = plan.pieces
    assert bed == {
        "nombre": "Cama",
        "ancho": 1.4,
        "alto": 0.5,
        "profundidad": 1.9,
        "posicion_x": 1.2,
        "posicion_y": 0.0,
        "posicion_z": 1.5,
    }
    assert (picture["nombre"], picture["posicion_y"], picture["ancho"]) == ("Cuadro", 1.3, 0.05)


def test_no_items_and_utf8_bom_are_fine():
    plan = load_project(b"\xef\xbb\xbf" + raw(project(items=[])))

    assert plan.pieces == []


def test_limits_are_inclusive():
    many_parts = item(PICTURE, parts=[PART] * 8)
    small = item(size_cm=[5, 5, 5], position={"x_m": 3, "z_m": 1, "elevation_m": 0, "rotation_deg": 0})
    big_room = {"height_m": 20, "vertices": circle(MAX_VERTICES, radius=20), "estimated": True}
    furniture = [item(small, name=f"Caja {n}") for n in range(200)]

    assert len(load_project(raw(project(items=[many_parts]))).pieces) == 1
    assert len(load_project(raw(project(room=big_room, items=furniture))).pieces) == 200


def test_room_limits_are_the_ones_of_post_rooms():
    # Decisión del usuario: cualquier proyecto exportable se puede reimportar (sin topes de 1,5-12 m).
    tiny = {"height_m": 0.3, "vertices": [[0, 0], [0.4, 0], [0.4, 0.4], [0, 0.4]], "estimated": False}
    far = {"height_m": 19.5, "vertices": [[-1000, -1000], [1000, -1000], [1000, 1000]], "estimated": False}

    assert load_project(raw(project(room=tiny, items=[]))).room["alto"] == 0.3
    assert load_project(raw(project(room=far, items=[]))).room["ancho"] == 2000


def test_an_object_against_the_walls_of_a_concave_room_is_inside():
    sofa = item(
        position={"x_m": 0.7, "z_m": 3.05, "elevation_m": 0, "rotation_deg": 0}, size_cm=[140, 80, 90]
    )

    plan = load_project(raw(project(room={**VALID["room"], "vertices": L_ROOM}, items=[sofa])))

    assert plan.pieces[0]["posicion_z"] == 3.05


# --- JSON malformado ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"   ",
        b"{",
        b"{'format': 'habita-project'}",
        b"\xff\xfe{}",
        b'{"format": "habita-project", "version": 1, "name": "x", "items": [], "room": NaN}',
        b'{"format": "habita-project", "version": Infinity}',
        b'{"format": "habita-project", "format": "habita-project", "version": 1}',
        b"[" * 100_000 + b"]" * 100_000,
        b'{"a":' * 50_000 + b"1" + b"}" * 50_000,
    ],
    # Ids cortos: Windows no admite variables de entorno de más de 32 767 caracteres (PYTEST_CURRENT_TEST).
    ids=[
        "empty",
        "blank",
        "open",
        "quotes",
        "not_utf8",
        "nan",
        "infinity",
        "repeated",
        "deep_list",
        "deep_obj",
    ],
)
def test_malformed_json_is_rejected(body):
    assert rejected(body) == NOT_JSON


@pytest.mark.parametrize(
    "data",
    [[], 7, "habita-project", None, {}, {"version": 1}, project(format="habita"), project(format=MARKER)],
)
def test_other_documents_are_not_projects(data):
    assert rejected(data) == NOT_A_PROJECT


@pytest.mark.parametrize("version", [2, 0, -1, "1", 1.0, True, None, [1]])
def test_other_versions_are_rejected(version):
    assert rejected(project(version=version)) == WRONG_VERSION


def test_a_missing_version_is_rejected():
    data = project()
    del data["version"]

    assert rejected(data) == WRONG_VERSION


# --- Campos de más y que faltan -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "place"),
    [
        ((), "el proyecto"),
        (("room",), "la habitación"),
        (("items", 0), "el objeto 1"),
        (("items", 1, "position"), "la posición del objeto 2"),
        (("items", 1, "parts", 0), "la parte 1 del objeto 2"),
    ],
)
@pytest.mark.parametrize("key", ["id", "user_id", "email", MARKER])
def test_extra_fields_are_rejected_without_naming_them(path, place, key):
    data = project()
    target = data
    for step in path:
        target = target[step]
    target[key] = MARKER

    assert rejected(data) == f"El archivo tiene un campo que no se admite en {place}."


@pytest.mark.parametrize(
    ("path", "field", "place"),
    [
        ((), "name", "el proyecto"),
        ((), "items", "el proyecto"),
        (("room",), "estimated", "la habitación"),
        (("items", 0), "parts", "el objeto 1"),
        (("items", 0), "category", "el objeto 1"),
        (("items", 0, "position"), "elevation_m", "la posición del objeto 1"),
        (("items", 1, "parts", 0), "color", "la parte 1 del objeto 2"),
    ],
)
def test_missing_fields_are_rejected(path, field, place):
    data = project()
    target = data
    for step in path:
        target = target[step]
    del target[field]

    assert rejected(data) == f"Falta un campo obligatorio en {place}."


# --- Topes de las listas ------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [2, MAX_VERTICES + 1])
def test_vertex_count_is_3_to_the_limit_of_post_rooms(count):
    room = {"height_m": 2.5, "vertices": circle(count), "estimated": False}

    assert rejected(project(room=room, items=[])) == VERTEX_COUNT


def test_more_than_200_objects_are_rejected():
    assert rejected(project(items=[BED] * 201)) == ITEM_COUNT


def test_more_than_8_parts_are_rejected():
    assert rejected(project(items=[BED, item(PICTURE, parts=[PART] * 9)])) == too_many_parts(2)


# --- Nombres ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [f"<b>{MARKER}</b>", f"{MARKER}\x00", "", "   ", "x" * 121, 7, None])
def test_invalid_project_names(name):
    assert rejected(project(name=name)) == (
        "El nombre de el proyecto no es válido: texto plano de 1 a 120 caracteres, sin < ni >."
    )


@pytest.mark.parametrize("name", [f"<img src=x onerror={MARKER}()>", f"{MARKER}‮", "x" * 61, ""])
def test_invalid_object_names(name):
    assert rejected(project(items=[BED, item(name=name)])) == (
        "El nombre de el objeto 2 no es válido: texto plano de 1 a 60 caracteres, sin < ni >."
    )


def test_invalid_category():
    assert rejected(project(items=[item(category=f"<{MARKER}>")])) == (
        "El nombre de la categoría del objeto 1 no es válido: texto plano de 1 a 60 caracteres, sin < ni >."
    )


def test_project_names_are_trimmed_and_object_names_collapsed():
    plan = load_project(raw(project(name="  Mi sala  ", items=[item(name="  Mesa   de   noche ")])))

    assert (plan.name, plan.pieces[0]["nombre"]) == ("Mi sala", "Mesa de noche")


# --- Valores fuera de los límites o de otro tipo ------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"size_cm": [4.9, 50, 190]}, "Hay un valor no válido en el objeto 1 (size_cm)."),
        ({"size_cm": [140, 400.1, 190]}, "Hay un valor no válido en el objeto 1 (size_cm)."),
        ({"size_cm": [140, 50]}, "Hay un valor no válido en el objeto 1 (size_cm)."),
        ({"size_cm": ["140", 50, 190]}, "Hay un valor no válido en el objeto 1 (size_cm)."),
        ({"size_cm": [True, 50, 190]}, "Hay un valor no válido en el objeto 1 (size_cm)."),
        ({"kind": "ceiling"}, "Hay un valor no válido en el objeto 1 (kind)."),
        ({"kind": MARKER}, "Hay un valor no válido en el objeto 1 (kind)."),
        ({"wall": "up"}, "Hay un valor no válido en el objeto 1 (wall)."),
        ({"parts": "none"}, "Hay un valor no válido en el objeto 1 (parts)."),
    ],
)
def test_invalid_object_values(changes, message):
    assert rejected(project(items=[item(**changes)])) == message


@pytest.mark.parametrize(
    ("position", "field"),
    [
        ({"rotation_deg": 90}, "rotation_deg"),
        ({"elevation_m": -0.1}, "elevation_m"),
        ({"x_m": 1000.5}, "x_m"),
        ({"z_m": "1.5"}, "z_m"),
    ],
)
def test_invalid_positions(position, field):
    bed = item(position={**BED["position"], **position})

    assert rejected(project(items=[bed])) == f"Hay un valor no válido en la posición del objeto 1 ({field})."


def test_a_huge_number_read_as_infinity_is_rejected_by_the_schema():
    body = raw(project(items=[BED])).replace(b'"x_m": 1.2', b'"x_m": 1e400')

    assert rejected(body) == "Hay un valor no válido en la posición del objeto 1 (x_m)."


@pytest.mark.parametrize(
    ("part", "field"),
    [
        ({"shape": "cone"}, "shape"),
        ({"color": "#GGGGGG"}, "color"),
        ({"color": f"#{MARKER}"}, "color"),
        ({"size_cm": [0.1, 10, 10]}, "size_cm"),
        ({"offset_cm": [0, 0, 400.5]}, "offset_cm"),
    ],
)
def test_invalid_parts(part, field):
    picture = item(PICTURE, parts=[PART, {**PART, **part}])

    assert (
        rejected(project(items=[picture])) == f"Hay un valor no válido en la parte 2 del objeto 1 ({field})."
    )


@pytest.mark.parametrize(
    "room",
    [
        {"height_m": 0, "vertices": VALID["room"]["vertices"], "estimated": False},
        {"height_m": 20.1, "vertices": VALID["room"]["vertices"], "estimated": False},
        {"height_m": 2.5, "vertices": [[0, 0], [4, 0], [4, 3, 1], [0, 3]], "estimated": False},
        {"height_m": 2.5, "vertices": [[0, 0], [1001, 0], [4, 3]], "estimated": False},
        {"height_m": 2.5, "vertices": VALID["room"]["vertices"], "estimated": "no"},
    ],
)
def test_invalid_room_values(room):
    assert rejected(project(room=room, items=[])).startswith("Hay un valor no válido en la habitación (")


# --- Geometría ----------------------------------------------------------------------------------------


def test_a_self_intersecting_polygon_is_rejected():
    bow_tie = {"height_m": 2.5, "vertices": [[0, 0], [4, 3], [4, 0], [0, 3]], "estimated": False}

    assert rejected(project(room=bow_tie, items=[])) == INVALID_POLYGON


def test_a_degenerate_polygon_is_rejected():
    line = {"height_m": 2.5, "vertices": [[0, 0], [2, 0], [4, 0]], "estimated": False}

    assert rejected(project(room=line, items=[])) == INVALID_POLYGON


@pytest.mark.parametrize(
    "changes",
    [
        {"position": {"x_m": 1.2, "z_m": 1.5, "elevation_m": 0.3, "rotation_deg": 0}},
        {"wall": "left"},
    ],
)
def test_floor_objects_stay_on_the_floor_without_a_wall(changes):
    assert rejected(project(items=[BED, item(**changes)])) == floor_object_raised(2)


@pytest.mark.parametrize(
    ("x_m", "z_m"),
    [(3.5, 1.5), (-0.1, 1.5), (10, 10)],
)
def test_objects_outside_the_room_are_rejected(x_m, z_m):
    bed = item(position={"x_m": x_m, "z_m": z_m, "elevation_m": 0, "rotation_deg": 0})

    assert rejected(project(items=[bed])) == object_outside(1)


def test_an_object_in_the_notch_of_a_concave_room_is_outside():
    table = item(size_cm=[100, 75, 100], position={"x_m": 3, "z_m": 3, "elevation_m": 0, "rotation_deg": 0})

    assert rejected(project(room={**VALID["room"], "vertices": L_ROOM}, items=[table])) == object_outside(1)


def test_an_object_above_the_ceiling_is_rejected():
    shelf = item(PICTURE, position={**PICTURE["position"], "elevation_m": 2.2})

    assert rejected(project(items=[BED, shelf])) == object_above_ceiling(2)


def test_one_centimeter_of_rounding_is_tolerated():
    # Pegado a la pared izquierda con 5 mm de sobra hacia fuera y 5 mm por encima del techo.
    picture = item(PICTURE, position={"x_m": 0.02, "z_m": 1.5, "elevation_m": 2.105, "rotation_deg": 0})

    assert load_project(raw(project(items=[picture]))).pieces[0]["posicion_y"] == 2.105


def test_a_floor_object_a_few_millimeters_up_is_stored_on_the_floor():
    bed = item(position={**BED["position"], "elevation_m": 0.004})

    assert load_project(raw(project(items=[bed]))).pieces[0]["posicion_y"] == 0.0
