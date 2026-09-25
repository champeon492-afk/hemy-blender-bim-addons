"""Validated Cartesian grid geometry, independent of Blender and IfcOpenShell.

Distances use the caller's coordinate units. U axes run horizontally, labelled
with letters by default. V axes run vertically, labelled with numbers.
"""

import math
import re


MAX_AXES = 200


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Axis count must be a whole number from 1 to 200.")
    if not 1 <= value <= MAX_AXES:
        raise ValueError("Axis count must be from 1 to 200.")
    return value


def _positive(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number.")
    return result


def _finite_coordinate(value):
    if not math.isfinite(value):
        raise ValueError("Grid dimensions are too large for finite coordinates.")
    return value


def positions(count, spacing, custom=""):
    """Return ``count`` cumulative axis coordinates, starting at zero.

    ``spacing`` is positive. ``custom`` is an optional comma-separated string
    of positive gaps. One custom gap repeats; otherwise there must be exactly
    ``count - 1`` gaps. Empty fields, NaN, infinity and zero gaps are rejected.
    """
    count = _count(count)
    spacing = _positive(spacing, "Spacing")
    if not isinstance(custom, str):
        raise ValueError("Custom spacing must be comma-separated numbers.")
    custom = custom.strip()
    if custom:
        gaps = [_positive(part.strip(), "Each custom gap") for part in custom.split(",")]
        if len(gaps) == 1:
            gaps *= count - 1
        elif len(gaps) != count - 1:
            raise ValueError(f"Enter one repeating gap or exactly {count - 1} gaps for {count} axes.")
    else:
        gaps = [spacing] * (count - 1)
    result = [0.0]
    for gap in gaps:
        result.append(_finite_coordinate(result[-1] + gap))
    return result


def _letter_label(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _validate_tags(tags):
    for tag in tags:
        if not tag or any(ord(character) < 32 or ord(character) == 127 for character in tag):
            raise ValueError("Axis labels must be nonempty and contain no control characters.")
    if len({tag.casefold() for tag in tags}) != len(tags):
        raise ValueError("Axis labels must be unique (ignoring letter case).")
    return tags


def labels(start, count):
    """Return sequential axis tags or a supplied comma-separated tag list.

    Letter sequences are uppercased and use spreadsheet progression Z -> AA.
    Integer sequences preserve leading zeroes (e.g. 009 -> 010). An explicit
    comma-separated list must have ``count`` unique, nonempty tags. With one
    axis, a non-sequential tag such as ``CL-1`` is also accepted.
    """
    count = _count(count)
    if not isinstance(start, str):
        raise ValueError("Starting label must be letters, an integer, or a comma-separated label list.")
    start = start.strip()
    if "," in start:
        result = [tag.strip() for tag in start.split(",")]
        if len(result) != count:
            raise ValueError(f"Enter exactly {count} explicit axis labels.")
    elif re.fullmatch(r"[A-Za-z]+", start):
        number = 0
        for character in start.upper():
            number = number * 26 + ord(character) - ord("A") + 1
        result = [_letter_label(number + index) for index in range(count)]
    elif re.fullmatch(r"[+-]?[0-9]+", start):
        digits = start.lstrip("+-")
        width = len(digits) if len(digits) > 1 and digits.startswith("0") else 0
        first = int(start)
        result = []
        for index in range(count):
            number = first + index
            result.append(("-" if number < 0 else "") + str(abs(number)).zfill(width))
    elif count == 1:
        result = [start]
    else:
        raise ValueError("Use a letter or integer starting label, or provide one comma-separated label per axis.")
    return _validate_tags(result)


def cartesian_axes(
    u_count,
    v_count,
    u_spacing,
    v_spacing,
    extension,
    u_gaps="",
    v_gaps="",
    u_start="A",
    v_start="1",
):
    """Return ``{'U': [...], 'V': [...]}`` with local 3D grid-axis segments.

    Each segment is ``{'tag': str, 'start': (x, y, z), 'end': (x, y, z)}``.
    U spacing is measured along Y; V spacing is measured along X. Extension
    extends both ends of every axis beyond the outermost perpendicular axes.
    All tags must be unique across the entire grid. Rotation and placement are
    deliberately left to the caller.
    """
    extension = _positive(extension, "Extension")
    u_positions = positions(u_count, u_spacing, u_gaps)
    v_positions = positions(v_count, v_spacing, v_gaps)
    u_tags = labels(u_start, u_count)
    v_tags = labels(v_start, v_count)
    _validate_tags(u_tags + v_tags)
    x_end = _finite_coordinate(v_positions[-1] + extension)
    y_end = _finite_coordinate(u_positions[-1] + extension)
    return {
        "U": [
            {"tag": tag, "start": (-extension, y, 0.0), "end": (x_end, y, 0.0)}
            for tag, y in zip(u_tags, u_positions)
        ],
        "V": [
            {"tag": tag, "start": (x, -extension, 0.0), "end": (x, y_end, 0.0)}
            for tag, x in zip(v_tags, v_positions)
        ],
    }
