import json
import math

import pytest

from app.schemas.photo import (
    MAX_DESCRIPTION_LENGTH,
    REASON_INVALID,
    REASON_LIMIT,
    REASON_OUTSIDE,
    UNNAMED,
    EstimateError,
    build_photo_plan,
    clean_plain_text,
)

ROOM = {"width_m": 4.0, "length_m": 3.0, "height_m": 2.5}


def thing(name="Sofá", **overrides):
    """Un objeto de suelo válido de 200 x 90 x 85 cm con centro en (1.5, 1)."""
    values = {
        "name": name,
        "kind": "floor",
        "width_cm": 200,
        "depth_cm": 90,
        "height_cm": 85,
        "x_m": 1.5,
        "z_m": 1.0,
        "elevation_m": 0,
    }
    return {**values, **overrides}


def reply(objects=(), room=None, **extra):
    return {"room": room or ROOM, "description": "Una sala.", "objects": list(objects), **extra}


# --- Caso válido ----------------------------------------------------------------------------------


def test_a_valid_estimate_is_converted_to_meters():
    plan = build_photo_plan(reply([thing()]))

    assert (plan.room.width_m, plan.room.length_m, plan.room.height_m) == (4.0, 3.0, 2.5)
    assert plan.description == "Una sala."
    (sofa,) = plan.objects
    assert sofa.name == "Sofá"
    assert (sofa.width_m, sofa.depth_m, sofa.height_m) == pytest.approx((2.0, 0.9, 0.85))
    assert (sofa.center_x, sofa.center_z, sofa.elevation_m) == pytest.approx((1.5, 1.0, 0.0))
    assert plan.discarded == []


def test_integers_are_accepted_as_numbers_and_objects_may_be_missing():
    plan = build_photo_plan({"room": {"width_m": 4, "length_m": 3, "height_m": 3}})

    assert plan.objects == [] and plan.description == ""


def test_a_wall_object_keeps_its_elevation_and_a_floor_one_is_put_on_the_floor():
    plan = build_photo_plan(
        reply(
            [
                thing("Cuadro", kind="wall", depth_cm=5, height_cm=60, elevation_m=1.4),
                thing("Mesa", elevation_m=0.7),
            ]
        )
    )

    cuadro, mesa = plan.objects
    assert cuadro.elevation_m == pytest.approx(1.4)
    assert mesa.elevation_m == 0


# --- Sobre inválido: la estimación entera se rechaza ------------------------------------------------


@pytest.mark.parametrize(
    "room",
    [
        {"width_m": 1.4, "length_m": 3, "height_m": 2.5},
        {"width_m": 12.1, "length_m": 3, "height_m": 2.5},
        {"width_m": 4, "length_m": 1.4, "height_m": 2.5},
        {"width_m": 4, "length_m": 3, "height_m": 1.9},
        {"width_m": 4, "length_m": 3, "height_m": 4.1},
        {"width_m": math.nan, "length_m": 3, "height_m": 2.5},
        {"width_m": math.inf, "length_m": 3, "height_m": 2.5},
        {"width_m": "4", "length_m": 3, "height_m": 2.5},
        {"width_m": 4, "length_m": 3},
        {"width_m": 4, "length_m": 3, "height_m": 2.5, "doors": 2},
    ],
)
def test_a_room_out_of_limits_or_malformed_is_rejected(room):
    with pytest.raises(EstimateError):
        build_photo_plan(reply(room=room))


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "texto",
        {"objects": []},
        reply(extra_field=1),
        {**reply(), "objects": "no es lista"},
        {**reply(), "description": 42},
    ],
)
def test_a_malformed_envelope_is_rejected(data):
    with pytest.raises(EstimateError):
        build_photo_plan(data)


def test_nan_parsed_from_the_model_json_is_rejected():
    # json.loads acepta NaN e Infinity; el esquema no.
    data = json.loads('{"room": {"width_m": NaN, "length_m": 3, "height_m": 2.5}}')

    with pytest.raises(EstimateError):
        build_photo_plan(data)


def test_the_error_never_contains_the_model_output():
    secret = "<texto del modelo que no debe salir>"

    with pytest.raises(EstimateError) as raised:
        build_photo_plan(reply(room={"width_m": secret, "length_m": 3, "height_m": 2.5}))

    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


# --- Objetos inválidos: se descartan uno a uno -------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        thing(width_cm=4),
        thing(height_cm=401),
        thing(depth_cm=math.nan),
        thing(x_m=math.inf),
        thing(kind="ceiling"),
        thing(elevation_m=-0.1),
        thing(colour="#fff"),
        thing(width_cm="200"),
        {k: v for k, v in thing().items() if k != "x_m"},
    ],
)
def test_an_object_out_of_limits_is_discarded_and_the_rest_kept(bad):
    plan = build_photo_plan(reply([bad, thing("Mesa")]))

    assert [o.name for o in plan.objects] == ["Mesa"]
    assert plan.discarded == [("Sofá", REASON_INVALID)]


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * 61, "<script>alert(1)</script>", "Sofá\x00", "Sofá​", 42, None],
)
def test_names_that_are_not_plain_text_are_discarded_without_echo(name):
    plan = build_photo_plan(reply([thing(name)]))

    assert plan.objects == []
    assert plan.discarded == [(UNNAMED, REASON_INVALID)]


def test_names_are_trimmed_and_their_spaces_collapsed():
    plan = build_photo_plan(reply([thing("  Mesa \n  de   centro ")]))

    assert plan.objects[0].name == "Mesa de centro"
    assert clean_plain_text("x" * 60) == "x" * 60


def test_garbage_items_in_the_list_are_discarded_without_name():
    plan = build_photo_plan(reply(["texto", 3, None, thing()]))

    assert [o.name for o in plan.objects] == ["Sofá"]
    assert plan.discarded == [(UNNAMED, REASON_INVALID)] * 3


# --- Colocación dentro de la sala --------------------------------------------------------------------


def test_an_object_up_to_10_cm_outside_is_moved_inside():
    # Ancho 2 m con centro en 0.92: empieza en -0.08 -> se desplaza 8 cm.
    plan = build_photo_plan(reply([thing(x_m=0.92, z_m=2.6)]))

    (sofa,) = plan.objects
    assert sofa.center_x == pytest.approx(1.0)
    assert sofa.center_z == pytest.approx(3.0 - 0.45)


def test_an_object_more_than_10_cm_outside_is_discarded():
    plan = build_photo_plan(reply([thing(x_m=0.85), thing("Mesa", z_m=3.2)]))

    assert plan.objects == []
    assert plan.discarded == [("Sofá", REASON_OUTSIDE), ("Mesa", REASON_OUTSIDE)]


def test_an_object_larger_than_the_room_is_discarded():
    plan = build_photo_plan(reply([thing(width_cm=400, x_m=2)], room={**ROOM, "width_m": 3.5}))

    assert plan.discarded == [("Sofá", REASON_OUTSIDE)]


def test_a_wall_object_through_the_ceiling_is_lowered_or_discarded():
    plan = build_photo_plan(
        reply(
            [
                thing("Cuadro", kind="wall", height_cm=60, elevation_m=1.95),  # sobra 5 cm
                thing("Repisa", kind="wall", height_cm=60, elevation_m=2.2),  # sobra 30 cm
            ]
        )
    )

    (cuadro,) = plan.objects
    assert cuadro.elevation_m == pytest.approx(1.9)
    assert plan.discarded == [("Repisa", REASON_OUTSIDE)]


def test_overlapping_objects_are_accepted():
    plan = build_photo_plan(reply([thing(), thing("Cojín", width_cm=40, depth_cm=40, height_cm=10)]))

    assert len(plan.objects) == 2


# --- Topes ---------------------------------------------------------------------------------------------


def test_only_10_objects_are_accepted():
    plan = build_photo_plan(reply([thing(f"Objeto {i}") for i in range(12)]))

    assert [o.name for o in plan.objects] == [f"Objeto {i}" for i in range(10)]
    assert plan.discarded == [("Objeto 10", REASON_LIMIT), ("Objeto 11", REASON_LIMIT)]


def test_only_the_first_30_objects_are_checked():
    plan = build_photo_plan(reply([thing(f"Objeto {i}") for i in range(50)]))

    assert len(plan.objects) + len(plan.discarded) == 30


def test_the_description_is_cleaned_and_cut():
    plan = build_photo_plan({**reply(), "description": "Sala\x07  luminosa " + "x" * 600})

    assert plan.description.startswith("Sala luminosa x")
    assert len(plan.description) == MAX_DESCRIPTION_LENGTH
