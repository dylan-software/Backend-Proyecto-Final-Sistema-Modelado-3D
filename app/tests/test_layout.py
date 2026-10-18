import pytest

from app.geometry.layout import (
    ItemPosition,
    LayoutItem,
    LayoutResult,
    footprint_center,
    footprint_corner,
    generate_layout,
    run_layout,
    validate_layout,
)
from app.geometry.room import Room
from app.tests.test_room import NOTCHED_ROOM


def rectangle_room():
    return Room([(0, 0), (4, 0), (4, 5), (0, 5)], 2.6)


def item(item_id, width_cm=100, depth_cm=60):
    return LayoutItem(item_id, f"Item {item_id}", width_cm / 100, depth_cm / 100)


def assert_inside(room, items, positions):
    by_id = {i.id: i for i in items}
    for p in positions:
        assert room.contains_rectangle(p.x, p.y, by_id[p.item_id].width_m, by_id[p.item_id].depth_m), p


# --- Ported from tests/pipeline.test.ts ("LayoutEngine") ------------------------------------------


def test_places_items_without_warnings_when_there_is_room():
    result = generate_layout(rectangle_room(), [item(1), item(2)], 15)

    assert len(result.positions) == 2
    assert result.warnings == []


def test_returns_warnings_instead_of_raising_when_there_is_no_room():
    items = [item(i, 150, 150) for i in range(20)]

    result = generate_layout(rectangle_room(), items, 15)

    assert result.warnings
    assert len(result.positions) < 20


def test_respects_an_irregular_room():
    l_shaped = Room([(0, 0), (4, 0), (4, 2), (2, 2), (2, 5), (0, 5)], 2.6)
    items = [item(1, 300, 100)]

    result = generate_layout(l_shaped, items, 15)

    assert_inside(l_shaped, items, result.positions)


# --- New ---------------------------------------------------------------------------------------


def test_keeps_the_minimum_distance_from_walls_and_between_items():
    result = generate_layout(rectangle_room(), [item(1), item(2)], 15)

    first, second = result.positions
    assert (first.x, first.y) == pytest.approx((0.15, 0.15))
    assert second.x == pytest.approx(first.x + 1.0 + 0.15)
    assert first.rotation == 0


def test_starts_a_new_row_when_the_current_one_is_full():
    result = generate_layout(rectangle_room(), [item(i, 150, 60) for i in range(3)], 15)

    assert [round(p.y, 2) for p in result.positions] == [0.15, 0.15, 0.9]


def test_never_places_furniture_in_the_notch_of_the_real_room():
    room = Room(NOTCHED_ROOM, 2.5)
    items = [
        item(i, w, d) for i, (w, d) in enumerate([(400, 50), (120, 60), (90, 90), (200, 40), (60, 60)] * 3)
    ]

    result = generate_layout(room, items, 10)

    assert result.positions
    assert_inside(room, items, result.positions)
    by_id = {i.id: i for i in items}
    for p in result.positions:
        width, depth = by_id[p.item_id].width_m, by_id[p.item_id].depth_m
        # Footprint overlapping the open rectangle x in (2, 4), y in (0, 1.5) would be in the notch.
        assert not (p.x < 4 and p.x + width > 2 and p.y < 1.5 and p.y + depth > 0), p


def test_a_piece_wider_than_the_room_is_left_out_with_a_warning():
    result = generate_layout(rectangle_room(), [item(1, 500, 50)], 15)

    assert result.positions == []
    assert "Item 1" in result.warnings[0]


# The reported case: in the notched room the row packer placed 2 of these 4 pieces and dropped the
# sofa and the chair, although both fit in the wide band below the notch (y between 1.5 and 3).
REPORTED_CATALOG = [
    LayoutItem(1, "Lámpara", 0.3, 0.3),
    LayoutItem(2, "Sofá", 2.0, 0.9),
    LayoutItem(3, "Mesa", 1.0, 0.6),
    LayoutItem(4, "Silla", 0.45, 0.5),
]


def assert_apart(items, positions, min_distance_cm):
    by_id = {i.id: i for i in items}
    for i, a in enumerate(positions):
        for b in positions[i + 1 :]:
            ia, ib = by_id[a.item_id], by_id[b.item_id]
            apart_x = max(b.x - (a.x + ia.width_m), a.x - (b.x + ib.width_m))
            apart_y = max(b.y - (a.y + ia.depth_m), a.y - (b.y + ib.depth_m))
            assert max(apart_x, apart_y) >= min_distance_cm / 100 - 1e-9, (a, b)


def assert_outside_the_notch(items, positions):
    by_id = {i.id: i for i in items}
    for p in positions:
        width, depth = by_id[p.item_id].width_m, by_id[p.item_id].depth_m
        # Open rectangle x in (2, 4), y in (0, 1.5), with slack for sums such as 1.55 + 0.45.
        assert not (p.x < 4 - 1e-9 and p.x + width > 2 + 1e-9 and p.y < 1.5 - 1e-9 and p.y + depth > 0), p


@pytest.mark.parametrize("min_distance_cm", [10, 20, 49, 52])
def test_a_piece_that_does_not_fit_in_its_row_is_placed_in_a_gap_further_down(min_distance_cm):
    room = Room(NOTCHED_ROOM, 2.5)

    result = generate_layout(room, REPORTED_CATALOG, min_distance_cm)

    assert result.warnings == []
    assert sorted(p.item_id for p in result.positions) == [1, 2, 3, 4]
    assert_inside(room, REPORTED_CATALOG, result.positions)
    assert_outside_the_notch(REPORTED_CATALOG, result.positions)
    assert_apart(REPORTED_CATALOG, result.positions, min_distance_cm)
    assert validate_layout(REPORTED_CATALOG, result.positions).valid is True


def test_the_reported_sofa_and_chair_go_below_the_notch():
    result = generate_layout(Room(NOTCHED_ROOM, 2.5), REPORTED_CATALOG, 49)

    by_id = {p.item_id: p for p in result.positions}
    sofa, chair = by_id[2], by_id[4]
    assert (sofa.x, sofa.y) == pytest.approx((0.49, 1.59))
    assert (chair.x, chair.y) == pytest.approx((1.29, 0.49))


def test_the_gap_search_is_deterministic():
    room = Room(NOTCHED_ROOM, 2.5)

    assert generate_layout(room, REPORTED_CATALOG, 49) == generate_layout(room, REPORTED_CATALOG, 49)


def test_a_piece_larger_than_every_gap_still_gets_a_warning():
    room = Room(NOTCHED_ROOM, 2.5)
    items = [*REPORTED_CATALOG, LayoutItem(5, "Armario", 3.0, 2.5)]

    result = generate_layout(room, items, 10)

    assert 5 not in {p.item_id for p in result.positions}
    assert len(result.warnings) == 1
    assert "'Armario' (id 5)" in result.warnings[0]
    assert sorted(p.item_id for p in result.positions) == [1, 2, 3, 4]


def test_pieces_placed_in_gaps_never_overlap_and_keep_their_distance():
    room = Room(NOTCHED_ROOM, 2.5)
    items = [
        item(i, w, d) for i, (w, d) in enumerate([(200, 90), (120, 60), (90, 90), (45, 50), (60, 60)] * 4)
    ]

    result = generate_layout(room, items, 15)

    assert result.warnings  # 20 pieces do not all fit: some are found in gaps, the rest are left out
    assert validate_layout(items, result.positions).valid is True
    assert_inside(room, items, result.positions)
    assert_outside_the_notch(items, result.positions)
    assert_apart(items, result.positions, 15)


# --- Ported from tests/pipeline.test.ts ("CollisionValidator") -------------------------------------

SQUARES = [item(1, 100, 100), item(2, 100, 100)]


def test_no_collision_without_overlap():
    result = validate_layout(SQUARES, [ItemPosition(1, 0, 0), ItemPosition(2, 2, 0)])

    assert result.valid is True


def test_tolerates_a_1_cm_overlap():
    # 1 cm along x but the full 1 m along y: the overlap depth is the SMALLER of the two.
    result = validate_layout(SQUARES, [ItemPosition(1, 0, 0), ItemPosition(2, 0.99, 0)])

    assert result.valid is True


def test_rejects_a_20_cm_overlap():
    result = validate_layout(SQUARES, [ItemPosition(1, 0, 0), ItemPosition(2, 0.8, 0)])

    assert result.valid is False
    assert len(result.collisions) == 1
    collision = result.collisions[0]
    assert (collision.item_a, collision.item_b, collision.overlap_cm) == (1, 2, 20.0)


def test_touching_pieces_do_not_collide():
    assert validate_layout(SQUARES, [ItemPosition(1, 0, 0), ItemPosition(2, 1, 0)]).valid is True


def test_unknown_item_is_an_error():
    with pytest.raises(ValueError, match="99"):
        validate_layout(SQUARES, [ItemPosition(99, 0, 0)])


# --- Ported from tests/pipeline.test.ts ("DecorationPipeline") -------------------------------------


def test_pipeline_is_ready_to_render_with_neutral_lighting():
    result = run_layout(rectangle_room(), [item(1)], 15)

    assert result.errors == []
    assert result.ready_to_render is True
    assert result.lighting["source"] == "neutral_preset"


def test_pipeline_never_trusts_a_generator_that_produces_collisions():
    items = [item(1, 200, 200), item(2, 200, 200)]

    def colliding(room, items, min_distance_cm):
        return LayoutResult(positions=[ItemPosition(1, 0, 0), ItemPosition(2, 0.1, 0)])

    result = run_layout(rectangle_room(), items, 15, generator=colliding)

    assert result.validation.valid is False
    assert result.ready_to_render is False


def test_pipeline_turns_a_generator_failure_into_an_error():
    def broken(room, items, min_distance_cm):
        raise RuntimeError("model unavailable")

    result = run_layout(rectangle_room(), [item(1)], 15, generator=broken)

    assert result.ready_to_render is False
    assert result.errors == ["Layout generation failed: model unavailable"]


def test_lighting_is_a_fresh_dict_every_time():
    first = run_layout(rectangle_room(), [], 15).lighting
    first["position"]["x"] = 99

    assert run_layout(rectangle_room(), [], 15).lighting["position"]["x"] == 0


# --- Corner (API) <-> center (database) -------------------------------------------------------------


def test_footprint_center_and_corner_are_inverse():
    assert footprint_center(1, 2, 0.5, 0.4) == (1.25, 2.2)
    assert footprint_corner(1.25, 2.2, 0.5, 0.4) == pytest.approx((1, 2))
