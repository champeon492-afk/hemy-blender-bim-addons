"""Screen-space, storey-filtered dimension graphics for orthographic plans.

The adapter owns IFC lookup, visibility and unit formatting. This module keeps
only numeric hitboxes between draws; it never retains Blender data-blocks.
"""

import math
import traceback

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


_handler = None
_hitboxes = {}
_reported_errors = set()
_PLAN_ALIGNMENT = 0.9999
_NORMAL_COLOR = (0.89, 0.94, 1.0, 1.0)
_SELECTED_COLOR = (1.0, 0.65, 0.20, 1.0)
_BACK_COLOR = (0.045, 0.055, 0.07, 0.90)


def is_plan(context):
    """True for top orthographic views, including downward ortho cameras."""
    if context.area is None or context.area.type != "VIEW_3D":
        return False
    region = context.region_data
    if region is None:
        # Sidebar buttons have a UI region; use their View3D space's view.
        region = getattr(context.space_data, "region_3d", None)
    if region is None:
        return False
    try:
        if region.view_perspective == "ORTHO":
            normal = region.view_rotation @ Vector((0.0, 0.0, 1.0))
        elif region.view_perspective == "CAMERA":
            space = context.space_data
            camera = (
                space.camera
                if getattr(space, "use_local_camera", False)
                else context.scene.camera
            )
            if camera is None or camera.data.type != "ORTHO":
                return False
            normal = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, 1.0))
        else:
            return False
        return normal.normalized().z >= _PLAN_ALIGNMENT
    except ReferenceError:
        # Deleting a camera can invalidate it during an in-flight redraw.
        return False


def _key(context):
    return (context.area.as_pointer(), context.region.as_pointer())


def _signature(context):
    """Reject hitboxes left over from a resized or navigated view."""
    region = context.region
    props = context.scene.BPDProperties
    return (
        region.width,
        region.height,
        props.storey_guid,
        tuple(value for row in context.region_data.perspective_matrix for value in row),
    )


def hit_test(context, x, y):
    """Return the ID of the last drawn label under region-local pixel x/y."""
    props = getattr(context.scene, "BPDProperties", None)
    if not props or not props.show_dimensions or not is_plan(context):
        return None
    if context.region is None or context.region.type != "WINDOW":
        return None
    entry = _hitboxes.get(_key(context))
    if entry is None or entry[0] != _signature(context):
        return None
    # The selected dimension is drawn last, so it wins overlapping labels.
    for dimension_id, left, bottom, right, top in reversed(entry[1]):
        if left <= x <= right and bottom <= y <= top:
            return dimension_id
    return None


def _project(context, xyz):
    point = location_3d_to_region_2d(context.region, context.region_data, Vector(xyz))
    if point is None or not all(math.isfinite(value) for value in point):
        return None
    return point


def _line(vertices, start, end):
    vertices.extend(((start.x, start.y, 0.0), (end.x, end.y, 0.0)))


def _witness(vertices, point, endpoint, scale):
    extension = endpoint - point
    if extension.length < 1.0:
        return
    direction = extension.normalized()
    gap = min(4.0 * scale, extension.length * 0.20)
    _line(vertices, point + direction * gap, endpoint + direction * 6.0 * scale)


def _draw_one(context, shader, dimension_id, label, measurement, selected, scale):
    points = [_project(context, xyz) for xyz in (
        measurement.start, measurement.end,
        measurement.line_start, measurement.line_end,
    )]
    if any(point is None for point in points):
        return None
    start, end, line_start, line_end = points
    delta = line_end - line_start
    if delta.length < 1.0:
        return None
    direction = delta.normalized()
    perpendicular = Vector((-direction.y, direction.x))
    vertices = []
    _line(vertices, line_start, line_end)
    _witness(vertices, start, line_start, scale)
    _witness(vertices, end, line_end, scale)
    slash = (direction + perpendicular).normalized() * 5.0 * scale
    for endpoint in (line_start, line_end):
        _line(vertices, endpoint - slash, endpoint + slash)

    color = _SELECTED_COLOR if selected else _NORMAL_COLOR
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": vertices}).draw(shader)

    font_id = 0
    blf.size(font_id, 13.0 * scale)
    text_width, text_height = blf.dimensions(font_id, label)
    midpoint = (line_start + line_end) * 0.5
    # A screen-horizontal label is easy to read for grids at every angle.
    left = midpoint.x - text_width * 0.5
    bottom = midpoint.y - text_height * 0.5
    padding = 5.0 * scale
    box = (
        left - padding, bottom - padding,
        left + text_width + padding, bottom + text_height + padding,
    )
    if box[2] < 0 or box[0] > context.region.width or box[3] < 0 or box[1] > context.region.height:
        return None
    x0, y0, x1, y1 = box
    background = ((x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0))
    shader.bind()
    shader.uniform_float("color", _BACK_COLOR)
    batch_for_shader(shader, "TRIS", {"pos": background}, indices=((0, 1, 2), (0, 2, 3))).draw(shader)
    blf.position(font_id, left, bottom, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, label)
    return (dimension_id, *box)


def _draw():
    context = bpy.context
    if context.area is None or context.region is None:
        return
    key = _key(context)
    # Clear before every early return, avoiding invisible but clickable labels.
    _hitboxes.pop(key, None)
    props = getattr(context.scene, "BPDProperties", None)
    if not props or not props.show_dimensions or not is_plan(context):
        return
    from . import adapter

    blend = gpu.state.blend_get()
    width = gpu.state.line_width_get()
    try:
        measurements = list(adapter.visible_measurements(context))
        measurements.sort(key=lambda entry: bool(entry[3] or entry[0] == props.active_dimension))
        # Blender leaves the computed DPI scale at zero in background mode.
        scale = context.preferences.system.ui_scale or context.preferences.view.ui_scale or 1.0
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(max(1.0, min(2.0, scale)))
        hitboxes = []
        for dimension_id, label, measurement, selected in measurements:
            try:
                hitbox = _draw_one(
                    context, shader, dimension_id, label, measurement,
                    selected or dimension_id == props.active_dimension, scale,
                )
                if hitbox is not None:
                    hitboxes.append(hitbox)
            except ReferenceError:
                # Objects can be deleted while Blender queues a viewport redraw.
                continue
        if len(_hitboxes) > 128:
            _hitboxes.clear()
        _hitboxes[key] = (_signature(context), hitboxes)
    except ReferenceError:
        pass
    except Exception as error:
        # A draw handler must not flood the console every frame. Preserve the
        # traceback once per error type/message so unexpected bugs are visible.
        error_key = (type(error).__name__, str(error))
        if error_key not in _reported_errors:
            if len(_reported_errors) >= 32:
                _reported_errors.clear()
            _reported_errors.add(error_key)
            print("Bonsai Plan Dimensions overlay error:")
            traceback.print_exc()
    finally:
        gpu.state.line_width_set(width)
        gpu.state.blend_set(blend)


def register():
    global _handler
    if _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")


def unregister():
    global _handler
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, "WINDOW")
        _handler = None
    _hitboxes.clear()
    _reported_errors.clear()
