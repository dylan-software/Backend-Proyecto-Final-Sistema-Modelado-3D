"""Coherencia espacial aplicada a la respuesta de la IA (build_photo_plan), sin IA real."""

import pytest

from app.geometry.arrangement import overlaps
from app.schemas.photo import build_photo_plan
from app.services.photo_estimator import SYSTEM_PROMPT

ROOM = {"width_m": 4.0, "length_m": 3.0, "height_m": 2.5}


def obj(name, kind="floor", w=100, d=100, h=50, x=2.0, z=1.5, elevation=0):
    return {
        "name": name,
        "kind": kind,
        "width_cm": w,
        "depth_cm": d,
        "height_cm": h,
        "x_m": x,
        "z_m": z,
        "elevation_m": elevation,
    }


def plan_for(*objects):
    return build_photo_plan({"room": ROOM, "description": "", "objects": list(objects)})


def named(plan):
    return {item.name: item for item in plan.objects}


def test_the_laptop_the_model_put_inside_the_bed_ends_on_top_of_it():
    plan = plan_for(obj("Cama", w=160, d=200, h=50, z=1.0), obj("Portátil", w=35, d=25, h=5, z=1.0))

    assert named(plan)["Portátil"].elevation_m == pytest.approx(0.5)
    assert plan.discarded == []


def test_the_mirror_inside_the_bed_ends_on_the_wall_above_it():
    plan = plan_for(
        obj("Cama", w=160, d=200, h=50, z=1.0),
        obj("Espejo", kind="wall", w=80, d=5, h=100, z=0.8, elevation=0.2),
    )

    mirror = named(plan)["Espejo"]
    assert mirror.center_z == pytest.approx(0.025)
    assert mirror.elevation_m == pytest.approx(0.5)
    assert not overlaps(mirror, named(plan)["Cama"])


def test_a_door_near_a_side_wall_is_set_on_it_turned_and_on_the_floor():
    plan = plan_for(obj("Puerta", kind="wall", w=90, d=10, h=210, x=3.85, z=1.5, elevation=0.3))

    door = named(plan)["Puerta"]
    assert (door.width_m, door.depth_m) == pytest.approx((0.1, 0.9))
    assert door.center_x == pytest.approx(3.95)
    assert door.elevation_m == 0


def test_two_overlapping_beds_are_separated_or_the_second_is_discarded():
    separated = plan_for(obj("Cama 1", w=160, d=200, x=1.0, z=1.0), obj("Cama 2", w=160, d=200, x=1.5, z=1.0))
    assert not overlaps(*separated.objects)

    crowded = build_photo_plan(
        {
            "room": {"width_m": 2.0, "length_m": 2.2, "height_m": 2.5},
            "objects": [obj("Cama 1", w=160, d=200, x=1.0, z=1.1), obj("Cama 2", w=160, d=200, x=1.1, z=1.1)],
        }
    )
    assert [item.name for item in crowded.objects] == ["Cama 1"]
    assert crowded.discarded == [("Cama 2", "se solapa con Cama 1")]


def test_a_wall_object_whose_center_is_outside_the_room_is_still_discarded():
    plan = plan_for(obj("Cuadro", kind="wall", w=80, d=5, h=60, x=4.5, z=0.02, elevation=1.2))

    assert plan.objects == []
    assert plan.discarded == [("Cuadro", "fuera de la habitación")]


def test_the_same_reply_gives_the_same_plan():
    reply = [
        obj("Cama", w=160, d=200, h=50, z=1.0),
        obj("Portátil", w=35, d=25, h=5, z=1.0),
        obj("Cama 2", w=160, d=200, x=2.5, z=1.0),
        obj("Espejo", kind="wall", w=80, d=5, h=100, z=0.8, elevation=0.2),
        obj("Puerta", kind="wall", w=90, d=10, h=210, x=3.85),
    ]

    assert plan_for(*reply) == plan_for(*reply)


def test_validation_discards_come_before_overlap_discards():
    crowded = build_photo_plan(
        {
            "room": {"width_m": 2.0, "length_m": 2.2, "height_m": 2.5},
            "objects": [
                obj("Cama 1", w=160, d=200, x=1.0, z=1.1),
                obj("Cama 2", w=160, d=200, x=1.1, z=1.1),
                obj("<b>", w=50),
            ],
        }
    )

    assert crowded.discarded == [
        ("(sin nombre)", "datos fuera de los límites"),
        ("Cama 2", "se solapa con Cama 1"),
    ]


def test_the_prompt_asks_for_spanish_names_and_wall_kind_for_doors_windows_mirrors_and_pictures():
    assert "name SIEMPRE en español" in SYSTEM_PROMPT
    assert "kind 'wall' SIEMPRE para puertas, ventanas, espejos y cuadros" in SYSTEM_PROMPT
