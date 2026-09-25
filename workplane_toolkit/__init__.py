# SPDX-License-Identifier: GPL-3.0-or-later
"""A persistent modeling frame with a viewport grid and reversible cursor setup."""

import bmesh
import bpy
import gpu
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
    IntProperty, PointerProperty, StringProperty,
)
from gpu_extras.batch import batch_for_shader
from mathutils import Euler, Matrix, Vector


bl_info = {
    "name": "Workplane Toolkit",
    "author": "Workplane Toolkit contributors",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Workplane",
    "description": "Set a modeling plane from faces, the cursor, view, or world axes",
    "category": "3D View",
}

_draw_handle = None
_grid_cache = None
_shader = None
_EPS = 1e-12
_IDENTITY = tuple(value for row in Matrix.Identity(4) for value in row)


def redraw():
    wm = bpy.context.window_manager
    if wm:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def _redraw_property(self, context):
    redraw()


def _flatten(matrix):
    return tuple(value for row in matrix for value in row)


def _unflatten(values):
    return Matrix([tuple(values[i:i + 4]) for i in range(0, 16, 4)])


def _frame(origin, tangent, normal):
    """Build a right-handed, orthonormal frame, rejecting collapsed inputs."""
    z = Vector(normal)
    if z.length_squared < _EPS:
        raise ValueError("The selection has no usable face normal")
    z.normalize()
    x = Vector(tangent) - z * Vector(tangent).dot(z)
    if x.length_squared < _EPS:
        candidate = min((Vector((1, 0, 0)), Vector((0, 1, 0)),
                         Vector((0, 0, 1))), key=lambda axis: abs(axis.dot(z)))
        x = candidate - z * candidate.dot(z)
    x.normalize()
    y = z.cross(x).normalized()
    matrix = Matrix((x, y, z)).transposed().to_4x4()
    matrix.translation = origin
    return matrix


def frame_from_face(context):
    obj = context.edit_object
    if context.mode != 'EDIT_MESH' or obj is None or obj.type != 'MESH':
        raise ValueError("Enter mesh Edit Mode and select a face first")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.normal_update()
    selected = [face for face in bm.faces if face.select and not face.hide]
    active = bm.faces.active
    if active is not None and active.select and not active.hide:
        face = active
    elif len(selected) == 1:
        face = selected[0]
    elif not selected:
        raise ValueError("Select a face in Edit Mode")
    else:
        raise ValueError("Select one face, or make the desired face active")
    linear = obj.matrix_world.to_3x3()
    try:
        normal_matrix = linear.inverted().transposed()
    except ValueError as exc:
        raise ValueError("The object has zero scale; give it nonzero scale first") from exc
    if face.calc_area() <= _EPS:
        raise ValueError("The selected face has zero area")
    edges = [linear @ (loop.link_loop_next.vert.co - loop.vert.co)
             for loop in face.loops]
    tangent = max(edges, key=lambda edge: edge.length_squared)
    if tangent.length_squared < _EPS:
        raise ValueError("The selected face is too small or collapsed")
    # Inverse-transpose correctly handles rotation, shear, and nonuniform scale.
    return _frame(obj.matrix_world @ face.calc_center_median(), tangent,
                  normal_matrix @ face.normal)


def _view_region(context):
    if context.area is None or context.area.type != 'VIEW_3D':
        raise ValueError("Run this from a 3D Viewport")
    region = context.region_data
    if region is not None and isinstance(region, bpy.types.RegionView3D):
        return region
    if context.space_data.region_quadviews:
        raise ValueError("Exit Quad View to use this sidebar view command")
    return context.space_data.region_3d


def frame_from_source(context, source):
    if source == 'FACE':
        return frame_from_face(context)
    if source == 'CURSOR':
        return context.scene.cursor.matrix.copy()
    if source == 'VIEW':
        region = _view_region(context)
        matrix = region.view_rotation.to_matrix().to_4x4()
        matrix.translation = context.scene.cursor.location
        return matrix
    axes = {
        'XY': ((1, 0, 0), (0, 0, 1)),
        'XZ': ((1, 0, 0), (0, -1, 0)),
        'YZ': ((0, 1, 0), (1, 0, 0)),
    }
    if source not in axes:
        raise ValueError("Unknown workplane source")
    x, z = axes[source]
    return _frame(Vector((0, 0, 0)), Vector(x), Vector(z))


def settings_matrix(settings):
    matrix = Euler(settings.rotation, 'XYZ').to_matrix().to_4x4()
    matrix.translation = settings.origin
    return matrix


def _remember(scene):
    settings = scene.workplane_toolkit
    if settings.active:
        return
    cursor = scene.cursor
    settings.previous_cursor = _flatten(cursor.matrix)
    settings.previous_cursor_mode = cursor.rotation_mode
    settings.previous_euler = cursor.rotation_euler
    settings.previous_quaternion = cursor.rotation_quaternion
    settings.previous_axis_angle = cursor.rotation_axis_angle
    slot = scene.transform_orientation_slots[0]
    settings.previous_orientation = slot.type
    settings.previous_custom_name = (
        slot.custom_orientation.name if slot.custom_orientation else ""
    )
    settings.previous_pivot = scene.tool_settings.transform_pivot_point


def apply_frame(context, matrix, label):
    scene = context.scene
    settings = scene.workplane_toolkit
    _remember(scene)
    settings.origin = matrix.translation
    settings.rotation = matrix.to_euler('XYZ')
    settings.applied_frame = _flatten(matrix)
    settings.source_label = label
    scene.cursor.matrix = matrix
    scene.transform_orientation_slots[0].type = 'CURSOR'
    scene.tool_settings.transform_pivot_point = (
        'CURSOR' if settings.use_cursor_pivot else settings.previous_pivot
    )
    settings.active = True
    redraw()


def restore_scene(scene):
    settings = scene.workplane_toolkit
    if not settings.active:
        return
    cursor = scene.cursor
    cursor.matrix = _unflatten(settings.previous_cursor)
    cursor.rotation_mode = settings.previous_cursor_mode
    cursor.rotation_euler = settings.previous_euler
    cursor.rotation_quaternion = settings.previous_quaternion
    cursor.rotation_axis_angle = settings.previous_axis_angle
    slot = scene.transform_orientation_slots[0]
    try:
        slot.type = settings.previous_orientation
        if settings.previous_orientation == 'CUSTOM' and (
            slot.custom_orientation is None or
            slot.custom_orientation.name != settings.previous_custom_name
        ):
            slot.type = 'GLOBAL'
    except (TypeError, ValueError):
        slot.type = 'GLOBAL'
    scene.tool_settings.transform_pivot_point = settings.previous_pivot
    settings.active = False
    redraw()


class WORKPLANE_PG_settings(bpy.types.PropertyGroup):
    active: BoolProperty(default=False)
    source_label: StringProperty(default="")
    origin: FloatVectorProperty(name="Origin", subtype='TRANSLATION', unit='LENGTH')
    rotation: FloatVectorProperty(name="Rotation", subtype='EULER', unit='ROTATION')
    applied_frame: FloatVectorProperty(size=16, default=_IDENTITY)
    show_grid: BoolProperty(name="Show Workplane Grid", default=True, update=_redraw_property)
    grid_spacing: FloatProperty(name="Grid Spacing", default=1.0, min=0.0001,
                                soft_max=10.0, unit='LENGTH', update=_redraw_property)
    half_lines: IntProperty(name="Grid Half-width (Cells)", default=10, min=1,
                           max=100, update=_redraw_property)
    grid_opacity: FloatProperty(name="Grid Opacity", default=0.45, min=0.05,
                               max=1.0, subtype='FACTOR', update=_redraw_property)
    use_cursor_pivot: BoolProperty(name="Pivot at Workplane Origin", default=True,
                                  description="Use the workplane origin for rotate and scale; click Apply to update")
    plane_size: FloatProperty(name="Plane Size", default=2.0, min=0.0001, unit='LENGTH')
    previous_cursor: FloatVectorProperty(size=16, default=_IDENTITY)
    previous_cursor_mode: StringProperty(default='XYZ')
    previous_euler: FloatVectorProperty(size=3)
    previous_quaternion: FloatVectorProperty(size=4, default=(1, 0, 0, 0))
    previous_axis_angle: FloatVectorProperty(size=4, default=(0, 0, 1, 0))
    previous_orientation: StringProperty(default='GLOBAL')
    previous_custom_name: StringProperty(default="")
    previous_pivot: StringProperty(default='MEDIAN_POINT')


class WORKPLANE_OT_set(bpy.types.Operator):
    bl_idname = "workplane.set"
    bl_label = "Set Workplane"
    bl_description = "Set the workplane; selected faces require mesh Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(items=[
        ('FACE', "Selected Face", "Use the active selected face center and normal"),
        ('CURSOR', "3D Cursor", "Use the 3D cursor position and rotation"),
        ('VIEW', "Current View", "Use the view orientation at the 3D cursor"),
        ('XY', "World XY", "World XY plane at world origin"),
        ('XZ', "World XZ", "World XZ plane at world origin"),
        ('YZ', "World YZ", "World YZ plane at world origin"),
    ], default='FACE')

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode in {'OBJECT', 'EDIT_MESH'}

    def execute(self, context):
        try:
            matrix = frame_from_source(context, self.source)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        labels = {'FACE': 'Selected Face', 'CURSOR': '3D Cursor', 'VIEW': 'Current View',
                  'XY': 'World XY', 'XZ': 'World XZ', 'YZ': 'World YZ'}
        apply_frame(context, matrix, labels[self.source])
        return {'FINISHED'}


class WORKPLANE_OT_apply(bpy.types.Operator):
    bl_idname = "workplane.apply"
    bl_label = "Apply / Reapply Workplane"
    bl_description = "Apply the origin, rotation, and pivot settings, and restore Cursor transform axes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.workplane_toolkit
        apply_frame(context, settings_matrix(settings), "Custom")
        return {'FINISHED'}


class WORKPLANE_OT_restore(bpy.types.Operator):
    bl_idname = "workplane.restore"
    bl_label = "Restore Previous Setup"
    bl_description = "Disable the workplane and restore the cursor, transform orientation, and pivot saved before the first Set"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.scene.workplane_toolkit.active

    def execute(self, context):
        restore_scene(context.scene)
        return {'FINISHED'}


class WORKPLANE_OT_align_view(bpy.types.Operator):
    bl_idname = "workplane.align_view"
    bl_label = "Look at Workplane"
    bl_description = "Look straight at the workplane in orthographic view"

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and context.scene.workplane_toolkit.active
                and context.area is not None and context.area.type == 'VIEW_3D')

    def execute(self, context):
        try:
            region = _view_region(context)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        matrix = _unflatten(context.scene.workplane_toolkit.applied_frame)
        region.view_rotation = matrix.to_quaternion()
        region.view_location = matrix.translation
        region.view_perspective = 'ORTHO'
        redraw()
        return {'FINISHED'}


class WORKPLANE_OT_add_plane(bpy.types.Operator):
    bl_idname = "workplane.add_plane"
    bl_label = "Add Plane on Workplane"
    bl_description = "Add a mesh plane using the applied workplane position and rotation"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and context.scene.workplane_toolkit.active
                and context.mode in {'OBJECT', 'EDIT_MESH'})

    def execute(self, context):
        settings = context.scene.workplane_toolkit
        matrix = _unflatten(settings.applied_frame)
        return bpy.ops.mesh.primitive_plane_add(
            size=settings.plane_size, align='WORLD', location=matrix.translation,
            rotation=matrix.to_euler('XYZ'),
        )


def _grid_coordinates(half_lines, spacing):
    extent = half_lines * spacing
    points = []
    for i in range(-half_lines, half_lines + 1):
        if i == 0:
            continue
        p = i * spacing
        points.extend([(-extent, p, 0), (extent, p, 0),
                       (p, -extent, 0), (p, extent, 0)])
    return points


def _draw_grid():
    global _shader, _grid_cache
    context = bpy.context
    scene = context.scene
    space = context.space_data
    if (scene is None or space is None or space.type != 'VIEW_3D'
            or not space.overlay.show_overlays):
        return
    settings = scene.workplane_toolkit
    if not settings.active or not settings.show_grid:
        return
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    key = (settings.half_lines, settings.grid_spacing)
    if _grid_cache is None or _grid_cache[0] != key:
        extent = settings.half_lines * settings.grid_spacing
        _grid_cache = (key,
            batch_for_shader(_shader, 'LINES', {'pos': _grid_coordinates(*key)}),
            batch_for_shader(_shader, 'LINES', {'pos': [(-extent, 0, 0), (extent, 0, 0)]}),
            batch_for_shader(_shader, 'LINES', {'pos': [(0, -extent, 0), (0, extent, 0)]}),
        )
    blend = gpu.state.blend_get()
    depth = gpu.state.depth_test_get()
    depth_mask = gpu.state.depth_mask_get()
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(False)
        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(_unflatten(settings.applied_frame))
            _shader.bind()
            opacity = settings.grid_opacity
            for batch, color in zip(_grid_cache[1:], [
                (0.45, 0.65, 0.8, opacity),
                (1.0, 0.18, 0.12, min(1.0, opacity + 0.35)),
                (0.22, 0.85, 0.28, min(1.0, opacity + 0.35)),
            ]):
                _shader.uniform_float('color', color)
                batch.draw(_shader)
    finally:
        gpu.state.depth_mask_set(depth_mask)
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


class WORKPLANE_PT_panel(bpy.types.Panel):
    bl_label = "Workplane Toolkit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Workplane"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.workplane_toolkit
        layout.label(text=(settings.source_label if settings.active else "No active workplane"),
                     icon='ORIENTATION_CURSOR')
        col = layout.column(align=True)
        col.scale_y = 1.15
        col.operator("workplane.set", text="From Selected Face", icon='FACESEL').source = 'FACE'
        row = col.row(align=True)
        row.operator("workplane.set", text="3D Cursor").source = 'CURSOR'
        row.operator("workplane.set", text="Current View").source = 'VIEW'
        row = col.row(align=True)
        for source in ('XY', 'XZ', 'YZ'):
            row.operator("workplane.set", text=source).source = source
        layout.separator()
        layout.prop(settings, "origin")
        layout.prop(settings, "rotation")
        layout.prop(settings, "use_cursor_pivot")
        layout.operator("workplane.apply", icon='CHECKMARK')
        if settings.active:
            applied = _unflatten(settings.applied_frame)
            cursor = context.scene.cursor.matrix
            mismatch = any(abs(a - b) > 1e-5 for a, b in zip(_flatten(cursor), _flatten(applied)))
            if mismatch or context.scene.transform_orientation_slots[0].type != 'CURSOR':
                layout.label(text="Cursor or axes changed: reapply", icon='INFO')
            layout.label(text="Move in plane: G, Shift Z")
            layout.operator("workplane.align_view", icon='VIEW_ORTHO')
            row = layout.row(align=True)
            row.prop(settings, "plane_size", text="Size")
            row.operator("workplane.add_plane", text="Add Plane", icon='MESH_PLANE')
        box = layout.box()
        box.prop(settings, "show_grid")
        col = box.column()
        col.enabled = settings.show_grid
        col.prop(settings, "grid_spacing")
        col.prop(settings, "half_lines")
        col.prop(settings, "grid_opacity")
        layout.operator("workplane.restore", icon='LOOP_BACK')


_classes = (
    WORKPLANE_PG_settings, WORKPLANE_OT_set, WORKPLANE_OT_apply,
    WORKPLANE_OT_restore, WORKPLANE_OT_align_view, WORKPLANE_OT_add_plane,
    WORKPLANE_PT_panel,
)


def register():
    global _draw_handle
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.workplane_toolkit = PointerProperty(type=WORKPLANE_PG_settings)
    if not bpy.app.background and _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_grid, (), 'WINDOW', 'POST_VIEW')
    redraw()


def unregister():
    global _draw_handle, _grid_cache, _shader
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if hasattr(bpy.types.Scene, 'workplane_toolkit'):
        for scene in bpy.data.scenes:
            restore_scene(scene)
        del bpy.types.Scene.workplane_toolkit
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _grid_cache = None
    _shader = None
    redraw()


if __name__ == "__main__":
    register()
