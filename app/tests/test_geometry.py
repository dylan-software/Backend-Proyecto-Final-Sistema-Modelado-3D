import numpy as np

from app.geometry.collision import check_bounding_box_collision
from app.geometry.octree import OctreeNode
from app.utils.typing import SpatialIndex


def test_octree_query_matches_brute_force_and_keeps_payloads():
    rng = np.random.default_rng(3)
    points = rng.uniform(0, 4, size=(3000, 3))
    tree = OctreeNode.from_points(points, payloads=list(range(len(points))))

    box_min, box_max = np.array([0.5, 1.0, 1.5]), np.array([2.0, 2.5, 3.0])
    found = tree.query_box(box_min, box_max)
    expected = np.flatnonzero(np.all((points >= box_min) & (points <= box_max), axis=1))

    assert sorted(entry.payload for entry in found) == expected.tolist()
    for entry in found:
        assert np.array_equal(entry.point, points[entry.payload])


def test_octree_satisfies_spatial_index_protocol():
    assert isinstance(OctreeNode(np.zeros(3), 1.0), SpatialIndex)


def test_flush_placement_is_not_a_collision():
    # 0.1 + 0.2 == 0.30000000000000004: rounding must not push a piece "through" the wall.
    assert not check_bounding_box_collision([0.1, 0, 0], [0.1 + 0.2, 1, 1], (0.3, 2, 2))
    # A real overshoot is still caught.
    assert check_bounding_box_collision([0.1, 0, 0], [0.31, 1, 1], (0.3, 2, 2))


def test_touching_boxes_do_not_collide():
    obstacle = ([1.0, 0.0, 1.0], [2.0, 1.0, 2.0])
    assert not check_bounding_box_collision([2.0, 0, 1], [2.5, 1, 2], (4, 3, 5), obstacles=[obstacle])
    assert check_bounding_box_collision([1.9, 0, 1], [2.5, 1, 2], (4, 3, 5), obstacles=[obstacle])
