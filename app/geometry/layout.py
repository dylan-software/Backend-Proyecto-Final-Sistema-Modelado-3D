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

# Rooms longer than this many steps along a side are searched with a coarser step (see grid_step).
MAX_STEPS_PER_AXIS = 100

# Search budget in work units (one per room vertex and placed piece checked against a candidate), so
# the worst room the API accepts stays around a second: per layout, and per piece so that one piece
# that never fits cannot use up the whole layout's budget.
MAX_LAYOUT_WORK = 1_000_000
MAX_PIECE_WORK = 250_000

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


def grid_step(box):
    """Search step in meters: SHIFT_STEP_M, coarser in rooms whose longer side needs more than
    MAX_STEPS_PER_AXIS of them, so a large room never multiplies the candidates."""
    longest = max(box.max_x - box.min_x, box.max_y - box.min_y)
    return max(SHIFT_STEP_M, longest / MAX_STEPS_PER_AXIS)


def _steps(start, stop, step):
    """start, start + step, ... up to stop inclusive (nothing if stop < start)."""
    index = 0
    # Computed from the start each time instead of accumulating the step, which drifts in binary.
    while (value := start + index * step) <= stop + EPSILON:
        yield value
        index += 1


def _keeps_distance(x, y, item, placed, gap):
    """Whether a footprint at (x, y) is at least gap away from every placed one along x or along y."""
    for other_x, other_y, other in placed:
        apart_x = x + item.width_m + gap <= other_x + EPSILON or other_x + other.width_m + gap <= x + EPSILON
        apart_y = y + item.depth_m + gap <= other_y + EPSILON or other_y + other.depth_m + gap <= y + EPSILON
        if not (apart_x or apart_y):
            return False
    return True


def _names(items):
    return ", ".join(f"'{item.name}' (id {item.id})" for item in items)


def generate_layout(room, items, min_distance_cm, max_work=MAX_LAYOUT_WORK, max_piece_work=MAX_PIECE_WORK):
    """Pack the items in rows inside the room, keeping min_distance_cm between them and from the edges
    of the bounding box (not from inner walls: a piece may stand against the wall of a notch).

    Rows start at the bounding box's minimum corner. A piece that would overflow the row starts a new
    one. Because the room may be irregular, a piece that fits in the bounding box but not in the real
    polygon is shifted along x in grid_step steps within the same row. If no shift fits, the rows
    below are searched in grid_step steps along y, through the rest of the bounding box, for a gap
    that is inside the polygon and min_distance_cm away from every piece placed so far; the row cursor
    does not move for a piece placed that way. Every candidate is tried in a fixed order, so the same
    input always gives the same layout.

    Each candidate costs one unit per room vertex and per piece placed so far, roughly what checking
    it takes. A piece may spend max_piece_work units and the whole layout max_work. A piece that runs
    out of its own budget is left out with a warning; once the layout runs out, it stops and a single
    warning lists the pieces it left out. Never raises on an impossible layout: pieces that do not fit
    anywhere are left out with a warning.
    """
    box = room.bounding_box()
    step = grid_step(box)
    gap = cm_to_m(min_distance_cm)
    vertex_count = len(room.vertices)
    result = LayoutResult()
    placed = []  # (x, y, item) of every piece placed so far
    work_left = max_work
    cursor_x = box.min_x + gap
    cursor_y = box.min_y + gap
    row_depth = 0.0

    def candidates(item):
        """(x, y, in_row): first the current row from the cursor, then the rows below it."""
        max_x = box.max_x - gap - item.width_m
        for x in _steps(cursor_x, max_x, step):
            yield x, cursor_y, True
        for y in _steps(cursor_y, box.max_y - gap - item.depth_m, step):
            for x in _steps(box.min_x + gap, max_x, step):
                yield x, y, False

    for index, item in enumerate(items):
        if cursor_x + item.width_m > box.max_x - gap:
            cursor_x = box.min_x + gap
            cursor_y += row_depth + gap
            row_depth = 0.0

        spent = 0
        found = None
        stopped_by = None  # "layout" or "piece" when a budget ran out before a spot was found
        for x, y, in_row in candidates(item):
            cost = vertex_count + len(placed)
            if spent + cost > work_left:
                stopped_by = "layout"
                break
            if spent + cost > max_piece_work:
                stopped_by = "piece"
                break
            spent += cost
            if room.contains_rectangle(x, y, item.width_m, item.depth_m) and _keeps_distance(
                x, y, item, placed, gap
            ):
                found = (x, y, in_row)
                break
        work_left -= spent

        if found is not None:
            x, y, in_row = found
            result.positions.append(ItemPosition(item.id, x, y))
            placed.append((x, y, item))
            if in_row:
                cursor_x = x + item.width_m + gap
                row_depth = max(row_depth, item.depth_m)
        elif stopped_by == "layout":
            # This piece and the ones after it are given up together, without trying them.
            result.warnings.append(
                "Se alcanzó el límite de trabajo de la distribución; no se colocaron: "
                f"{_names(items[index:])}."
            )
            break
        elif stopped_by == "piece":
            result.warnings.append(
                f"Se detuvo la búsqueda de un hueco para {_names([item])} al llegar al límite de "
                "posiciones revisadas; no se colocó."
            )
        else:
            result.warnings.append(
                f"No hay espacio para colocar {_names([item])} dentro de la forma de la "
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
