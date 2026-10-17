"""Deterministic furniture layout inside the real room polygon.

Port of the Node src/domain/LayoutEngine.ts, CollisionValidator.ts, LightingSystem.ts and
DecorationPipeline.ts. The clear-path stage is not ported: no doors are persisted yet.

Positions are the MINIMUM corner of each footprint in floor-plan coordinates (meters; the plan's y is
the room's z axis), as in the API. The database stores the CENTER of the footprint: convert with
footprint_center and footprint_corner, never inline.
"""

from dataclasses import dataclass, field

from app.geometry.units import cm_to_m

# When a piece does not fit at the cursor, it is shifted along x in steps of this size before giving up.
SHIFT_STEP_M = 0.1

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


def generate_layout(room, items, min_distance_cm):
    """Pack the items in rows inside the room, keeping min_distance_cm between them and from the walls.

    Rows start at the bounding box's minimum corner. A piece that would overflow the row starts a new
    one. Because the room may be irregular, a piece that fits in the bounding box but not in the real
    polygon is shifted along x in SHIFT_STEP_M steps within the same row before it is given up. Never
    raises on an impossible layout: pieces that do not fit are left out with a warning.
    """
    box = room.bounding_box()
    gap = cm_to_m(min_distance_cm)
    result = LayoutResult()
    cursor_x = box.min_x + gap
    cursor_y = box.min_y + gap
    row_depth = 0.0

    for item in items:
        if cursor_x + item.width_m > box.max_x - gap:
            cursor_x = box.min_x + gap
            cursor_y += row_depth + gap
            row_depth = 0.0

        placed = False
        step = 0
        # Computed from the cursor each time instead of accumulating 0.1, which drifts in binary.
        try_x = cursor_x
        while try_x + item.width_m <= box.max_x - gap:
            if room.contains_rectangle(try_x, cursor_y, item.width_m, item.depth_m):
                result.positions.append(ItemPosition(item.id, try_x, cursor_y))
                cursor_x = try_x + item.width_m + gap
                row_depth = max(row_depth, item.depth_m)
                placed = True
                break
            step += 1
            try_x = cursor_x + step * SHIFT_STEP_M

        if not placed:
            if cursor_y + item.depth_m > box.max_y - gap:
                result.warnings.append(
                    f"No hay espacio para colocar '{item.name}' (id {item.id}) respetando la distancia "
                    f"mínima de {min_distance_cm:g} cm."
                )
            else:
                result.warnings.append(
                    f"'{item.name}' (id {item.id}) no cupo dentro de la forma de la habitación "
                    "en la fila actual."
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
