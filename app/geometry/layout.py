"""Deterministic furniture layout inside the real room polygon.

Port of the Node src/domain/LayoutEngine.ts, CollisionValidator.ts, LightingSystem.ts and
DecorationPipeline.ts. The clear-path stage is not ported: no doors are persisted yet.

Positions are the MINIMUM corner of each footprint in floor-plan coordinates (meters; the plan's y is
the room's z axis), as in the API. The database stores the CENTER of the footprint: convert with
footprint_center and footprint_corner, never inline.
"""

from dataclasses import dataclass, field

from app.geometry.units import cm_to_m

# When a piece does not fit at the cursor, it is shifted along x, then along y, in steps of this size.
SHIFT_STEP_M = 0.1

# Floating-point slack in meters for comparisons of sums such as x + width + gap.
EPSILON = 1e-9

# Overlaps up to this depth are scan measurement error, not a real collision.
OVERLAP_TOLERANCE_CM = 2.0


def neutral_lighting():
    """Lighting used until an estimator exists: a white light above the origin (a new dict each time)."""
    return {
        "position": {"x": 0.0, "y": 3.0, "z": 0.0},
        "intensity": 0.8,
        "color": "#ffffff",
        "source": "neutral_preset",
    }


@dataclass(frozen=True)
class LayoutItem:
    """A catalog piece to place: its id, name and footprint in meters."""

    id: int
    name: str
    width_m: float
    depth_m: float


@dataclass(frozen=True)
class ItemPosition:
    item_id: int
    x: float
    y: float
    rotation: float = 0.0


@dataclass
class LayoutResult:
    positions: list[ItemPosition] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Collision:
    item_a: int
    item_b: int
    overlap_cm: float


@dataclass
class ValidationResult:
    valid: bool
    collisions: list[Collision] = field(default_factory=list)


@dataclass
class PipelineResult:
    layout: LayoutResult
    validation: ValidationResult
    lighting: dict
    ready_to_render: bool
    errors: list[str]


def footprint_center(x, y, width_m, depth_m):
    """Center of a footprint given its minimum corner (API -> database)."""
    return x + width_m / 2, y + depth_m / 2


def footprint_corner(center_x, center_y, width_m, depth_m):
    """Minimum corner of a footprint given its center (database -> API)."""
    return center_x - width_m / 2, center_y - depth_m / 2


def _steps(start, stop):
    """start, start + SHIFT_STEP_M, ... up to stop inclusive (nothing if stop < start)."""
    step = 0
    while (value := start + step * SHIFT_STEP_M) <= stop + EPSILON:
        yield value
        step += 1


def _keeps_distance(x, y, item, placed, gap):
    """Whether a footprint at (x, y) is at least gap away from every placed one along x or along y."""
    for other_x, other_y, other in placed:
        apart_x = x + item.width_m + gap <= other_x + EPSILON or other_x + other.width_m + gap <= x + EPSILON
        apart_y = y + item.depth_m + gap <= other_y + EPSILON or other_y + other.depth_m + gap <= y + EPSILON
        if not (apart_x or apart_y):
            return False
    return True


def generate_layout(room, items, min_distance_cm):
    """Pack the items in rows inside the room, keeping min_distance_cm between them and from the walls.

    Rows start at the bounding box's minimum corner. A piece that would overflow the row starts a new
    one. Because the room may be irregular, a piece that fits in the bounding box but not in the real
    polygon is shifted along x in SHIFT_STEP_M steps within the same row. If no shift fits, the rows
    below are searched in SHIFT_STEP_M steps along y, through the rest of the bounding box, for a gap
    that is inside the polygon and min_distance_cm away from every piece placed so far; the row cursor
    does not move for a piece placed that way. Every candidate is tried in a fixed order, so the same
    input always gives the same layout. Never raises on an impossible layout: pieces that do not fit
    anywhere are left out with a warning.
    """
    box = room.bounding_box()
    gap = cm_to_m(min_distance_cm)
    result = LayoutResult()
    placed = []  # (x, y, item) of every piece placed so far
    cursor_x = box.min_x + gap
    cursor_y = box.min_y + gap
    row_depth = 0.0

    def fits(x, y, item):
        return room.contains_rectangle(x, y, item.width_m, item.depth_m) and _keeps_distance(
            x, y, item, placed, gap
        )

    def place(x, y, item):
        result.positions.append(ItemPosition(item.id, x, y))
        placed.append((x, y, item))

    for item in items:
        if cursor_x + item.width_m > box.max_x - gap:
            cursor_x = box.min_x + gap
            cursor_y += row_depth + gap
            row_depth = 0.0

        # Offsets are computed from the start each time instead of accumulating 0.1, which drifts in binary.
        row_x = next(
            (x for x in _steps(cursor_x, box.max_x - gap - item.width_m) if fits(x, cursor_y, item)), None
        )
        if row_x is not None:
            place(row_x, cursor_y, item)
            cursor_x = row_x + item.width_m + gap
            row_depth = max(row_depth, item.depth_m)
            continue

        gap_corner = next(
            (
                (x, y)
                for y in _steps(cursor_y, box.max_y - gap - item.depth_m)
                for x in _steps(box.min_x + gap, box.max_x - gap - item.width_m)
                if fits(x, y, item)
            ),
            None,
        )
        if gap_corner is not None:
            place(*gap_corner, item)
            continue

        result.warnings.append(
            f"No hay espacio para colocar '{item.name}' (id {item.id}) dentro de la forma de la "
            f"habitación respetando la distancia mínima de {min_distance_cm:g} cm."
        )
    return result


def validate_layout(items, positions):
    """Report the pairs of placed pieces whose footprints overlap by more than OVERLAP_TOLERANCE_CM.

    The overlap depth is the SMALLER of the overlaps along x and y: two pieces side by side that
    overlap 1 cm in x and 1 m in y are 1 cm into each other, not 1 m. Taking the larger one turned
    every slight contact into a collision.

    Raises:
        ValueError: a position refers to an item that is not in items.
    """
    by_id = {item.id: item for item in items}
    boxes = []
    for position in positions:
        item = by_id.get(position.item_id)
        if item is None:
            raise ValueError(f"item {position.item_id} was not found")
        boxes.append((position, position.x + item.width_m, position.y + item.depth_m))

    collisions = []
    for i, (a, a_max_x, a_max_y) in enumerate(boxes):
        for b, b_max_x, b_max_y in boxes[i + 1 :]:
            overlap_x = min(a_max_x, b_max_x) - max(a.x, b.x)
            overlap_y = min(a_max_y, b_max_y) - max(a.y, b.y)
            if overlap_x <= 0 or overlap_y <= 0:
                continue
            overlap_cm = min(overlap_x, overlap_y) * 100
            if overlap_cm <= OVERLAP_TOLERANCE_CM:
                continue
            collisions.append(Collision(a.item_id, b.item_id, round(overlap_cm, 1)))
    return ValidationResult(valid=not collisions, collisions=collisions)


def run_layout(room, items, min_distance_cm, generator=generate_layout):
    """Layout, collision check and lighting as isolated stages; never raises.

    The collision check always runs, even on a layout from another generator, which is never trusted.
    ready_to_render is true only when there are no collisions and no stage failed.
    """
    errors = []
    try:
        layout = generator(room, items, min_distance_cm)
    except Exception as exc:  # any generator failure becomes an explainable error
        errors.append(f"Layout generation failed: {exc}")
        layout = LayoutResult()

    try:
        validation = validate_layout(items, layout.positions)
    except ValueError as exc:
        errors.append(f"Collision validation failed: {exc}")
        validation = ValidationResult(valid=False)

    return PipelineResult(
        layout=layout,
        validation=validation,
        lighting=neutral_lighting(),
        ready_to_render=validation.valid and not errors,
        errors=errors,
    )
