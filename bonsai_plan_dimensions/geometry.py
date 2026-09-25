"""Pure plan geometry used by the Bonsai driving-dimension add-on.

All distances are in Blender world units (metres with Bonsai's normal setup).
The module deliberately has no Blender or IfcOpenShell dependencies. It models
straight, horizontal, parallel axes and optionally symmetric wall half-widths.
It does not infer a closest distance between longitudinally disjoint segments.
"""

from dataclasses import dataclass
import math
from typing import Iterable, Tuple


Vector = Tuple[float, float, float]
LENGTH_TOLERANCE = 1.0e-7
ANGULAR_TOLERANCE = 1.0e-4


def _finite(value, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _vector(value, label: str) -> Vector:
    try:
        coordinates = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{label} must contain three finite coordinates") from exc
    if len(coordinates) != 3:
        raise ValueError(f"{label} must contain three finite coordinates")
    return tuple(_finite(v, label) for v in coordinates)


def _dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b))


def _add_scaled(a: Vector, b: Vector, factor: float) -> Vector:
    return tuple(x + y * factor for x, y in zip(a, b))


@dataclass(frozen=True)
class PlanLine:
    """A finite straight axis; ``half_width`` is used only for CLEAR mode.

    Elevations of two lines may differ because measurement is in plan. Each
    individual line must be horizontal. Endpoint order has no significance.
    """

    start: Vector
    end: Vector
    half_width: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "start", _vector(self.start, "Line start"))
        object.__setattr__(self, "end", _vector(self.end, "Line end"))
        object.__setattr__(self, "half_width", _finite(self.half_width, "Half-width"))
        if self.half_width < 0:
            raise ValueError("Half-width cannot be negative")
        if self.length <= LENGTH_TOLERANCE:
            raise ValueError("A dimension requires a non-degenerate plan axis")
        if abs(self.end[2] - self.start[2]) > LENGTH_TOLERANCE:
            raise ValueError("A dimension requires a horizontal plan axis")

    @property
    def length(self) -> float:
        """Length of the XY projection."""
        return math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])

    @property
    def direction(self) -> Vector:
        """Canonical unit tangent, invariant under endpoint reversal.

        Its X coordinate is positive, or its Y coordinate is positive for an
        axis with effectively zero X component.
        """
        dx = (self.end[0] - self.start[0]) / self.length
        dy = (self.end[1] - self.start[1]) / self.length
        if dx < -LENGTH_TOLERANCE or (abs(dx) <= LENGTH_TOLERANCE and dy < 0):
            dx, dy = -dx, -dy
        return (dx, dy, 0.0)


@dataclass(frozen=True)
class Measurement:
    """Distance, element witnesses, and displaced annotation endpoints.

    ``normal`` points from A to B; ``tangent`` has canonical orientation.
    ``start``/``end`` lie on the measured axes or facing wall surfaces.
    ``line_start``/``line_end`` are shifted by ``offset`` along the tangent.
    All four points use the requested annotation elevation.
    """

    value: float
    normal: Vector
    start: Vector
    end: Vector
    line_start: Vector
    line_end: Vector
    tangent: Vector


def _parallel(a: PlanLine, b: PlanLine) -> None:
    ta, tb = a.direction, b.direction
    # Unit 2D cross product is sin(angle); this also accepts antiparallel axes.
    if abs(ta[0] * tb[1] - ta[1] * tb[0]) > ANGULAR_TOLERANCE:
        raise ValueError("Dimension axes must be parallel in plan")


def _span(line: PlanLine, tangent: Vector) -> Tuple[float, float]:
    return tuple(sorted((_dot(line.start, tangent), _dot(line.end, tangent))))


def _point_at(line: PlanLine, coordinate: float, tangent: Vector, z: float) -> Vector:
    begin = _dot(line.start, tangent)
    finish = _dot(line.end, tangent)
    proportion = (coordinate - begin) / (finish - begin)
    return (
        line.start[0] + proportion * (line.end[0] - line.start[0]),
        line.start[1] + proportion * (line.end[1] - line.start[1]),
        z,
    )


def measure(
    a: PlanLine,
    b: PlanLine,
    mode: str = "AXIS",
    offset: float = 1.0,
    z: float = 0.0,
) -> Measurement:
    """Measure perpendicular AXIS spacing or CLEAR space between two lines.

    Witnesses use the high end of the longitudinal span shared by both lines.
    ``offset`` may be positive, negative, or zero and moves the dimension line
    along the canonical tangent. CLEAR subtracts both symmetric half-widths.
    Coincident axes, touching/overlapping clear surfaces, nonparallel axes, and
    axes without longitudinal overlap raise ``ValueError``.
    """
    if mode not in ("AXIS", "CLEAR"):
        raise ValueError("Dimension mode must be AXIS or CLEAR")
    offset = _finite(offset, "Dimension offset")
    z = _finite(z, "Dimension elevation")
    _parallel(a, b)
    tangent = a.direction
    span_a, span_b = _span(a, tangent), _span(b, tangent)
    low, high = max(span_a[0], span_b[0]), min(span_a[1], span_b[1])
    if high < low - LENGTH_TOLERANCE:
        raise ValueError("Dimension axes must overlap along their lengths")
    witness_a = _point_at(a, high, tangent, z)
    witness_b = _point_at(b, high, tangent, z)
    normal = (-tangent[1], tangent[0], 0.0)
    separation = _dot(tuple(y - x for x, y in zip(witness_a, witness_b)), normal)
    if abs(separation) <= LENGTH_TOLERANCE:
        raise ValueError("Cannot dimension coincident axes")
    if separation < 0:
        normal = tuple(-v for v in normal)
    value = abs(separation)
    if mode == "CLEAR":
        value -= a.half_width + b.half_width
        if value <= LENGTH_TOLERANCE:
            raise ValueError("Wall surfaces overlap or touch; clear distance must be positive")
        witness_a = _add_scaled(witness_a, normal, a.half_width)
        witness_b = _add_scaled(witness_b, normal, -b.half_width)
    return Measurement(
        value=value,
        normal=normal,
        start=witness_a,
        end=witness_b,
        line_start=_add_scaled(witness_a, tangent, offset),
        line_end=_add_scaled(witness_b, tangent, offset),
        tangent=tangent,
    )


def motion(measurement: Measurement, target: float, moving: str = "B") -> Vector:
    """Return the world translation for A or B, retaining the original side.

    The fixed element is never moved. Changing a CLEAR distance preserves the
    original half-widths, because target minus current distance is the required
    axis translation for either mode. No IFC or Blender mutation occurs here.
    """
    target = _finite(target, "Target distance")
    if target <= 0:
        raise ValueError("Target distance must be positive")
    if moving not in ("A", "B"):
        raise ValueError("Moving element must be A or B")
    distance = _finite(measurement.value, "Measured distance")
    if distance <= 0:
        raise ValueError("Measured distance must be positive")
    change = target - distance
    if moving == "A":
        change = -change
    return tuple(component * change for component in measurement.normal)


def chain(lines: Iterable[PlanLine]) -> list[int]:
    """Order parallel axes along the canonical left normal for gap dimensions.

    Returns original zero-based indices. Every adjacent pair must support AXIS
    measurement, so duplicate axes and longitudinal gaps raise ``ValueError``.
    An empty input or one axis returns an empty or singleton index list.
    """
    lines = list(lines)
    if not lines:
        return []
    tangent = lines[0].direction
    normal = (-tangent[1], tangent[0], 0.0)
    for line in lines[1:]:
        _parallel(lines[0], line)
    order = sorted(
        range(len(lines)),
        key=lambda index: (
            (_dot(lines[index].start, normal) + _dot(lines[index].end, normal)) / 2,
            index,
        ),
    )
    for a_index, b_index in zip(order, order[1:]):
        measure(lines[a_index], lines[b_index])
    return order
