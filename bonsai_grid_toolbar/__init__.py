# SPDX-License-Identifier: GPL-3.0-or-later
"""Revit-style grid authoring using Bonsai's native IFC transactions."""
bl_info = {
    'name': 'Bonsai IFC Grid Toolbar', 'author': 'Codex', 'version': (1, 1, 0),
    'blender': (4, 2, 0), 'location': '3D View > Toolbar / N > IFC Grid',
    'description': 'Create IFC grids, draw axes, offset and rename axes with label bubbles',
    'category': '3D View',
}

import math
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix, Vector
from .geometry import cartesian_axes, labels

_handle = None
_preview = None
_stretch_preview = None


def api():
    import bonsai.tool as tool
    return tool


def ready(context):
    try:
        return context.mode == 'OBJECT' and bool(api().Ifc.get())
    except (ImportError, AttributeError):
        return False


def active_grid(context):
    if not ready(context) or not context.active_object:
        return None
    entity = api().Ifc.get_entity(context.active_object)
    if entity and entity.is_a('IfcGrid'):
        return entity
    if entity and entity.is_a('IfcGridAxis'):
        owners = entity.PartOfU or entity.PartOfV or entity.PartOfW
        return owners[0] if owners else None
    return None


def active_axis(context):
    if ready(context) and context.active_object:
        entity = api().Ifc.get_entity(context.active_object)
        if entity and entity.is_a('IfcGridAxis'):
            return entity


def all_axes(grid):
    return tuple(grid.UAxes or ()) + tuple(grid.VAxes or ()) + tuple(grid.WAxes or ())


def next_tag(grid, group):
    used = {str(a.AxisTag).casefold() for a in all_axes(grid)}
    start = 'A' if group == 'U' else '1'
    # Labels are intentionally deterministic, including gaps after deleted axes.
    for n in range(1, 10001):
        tag = labels(start, min(n, 200))[-1] if n <= 200 else (str(n) if group == 'V' else 'U' + str(n))
        if tag.casefold() not in used:
            return tag
    raise ValueError('No unused axis label available')


def validate_tag(grid, tag, exclude=None):
    tag = tag.strip()
    if not tag or len(tag) > 32 or any(c in tag for c in '/\\\n\r\t'):
        raise ValueError('Use a label of 1–32 characters without slashes or line breaks')
    if any(a != exclude and (a.AxisTag or '').casefold() == tag.casefold() for a in all_axes(grid)):
        raise ValueError('This grid already has an axis named ' + tag)
    return tag


def select_objects(context, objects):
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = objects[0]


def add_axis(context, grid, group, tag, start, end):
    tool = api()
    import ifcopenshell.api.grid
    grid_obj = tool.Ifc.get_object(grid)
    mesh = bpy.data.meshes.new('Grid Axis')
    mesh.from_pydata([start, end], [(0, 1)], [])
    obj = bpy.data.objects.new('IfcGridAxis/' + tag, mesh)
    obj.matrix_world = grid_obj.matrix_world.copy()
    obj.show_in_front = True
    axis = ifcopenshell.api.grid.create_grid_axis(tool.Ifc.get(), grid=grid, axis_tag=tag, uvw_axes=group + 'Axes')
    tool.Ifc.link(axis, obj)
    tool.Model.create_axis_curve(obj, axis)
    tool.Collector.assign(obj)
    return obj


def refresh():
    api().Root.reload_grid_decorator()
    install_overlay()
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class IFCGRID_PG_settings(bpy.types.PropertyGroup):
    name: StringProperty(name='Grid name', default='Structural Grid')
    u_count: IntProperty(name='Horizontal axes', default=4, min=1, max=200)
    v_count: IntProperty(name='Vertical axes', default=5, min=1, max=200)
    u_spacing: FloatProperty(name='Row spacing', default=6, min=0.001, subtype='DISTANCE')
    v_spacing: FloatProperty(name='Column spacing', default=6, min=0.001, subtype='DISTANCE')
    u_start: StringProperty(name='First label / list', default='A')
    v_start: StringProperty(name='First label / list', default='1')
    u_gaps: StringProperty(name='Custom gaps (m)', description='Optional: one repeated gap or count minus one comma-separated gaps in metres')
    v_gaps: StringProperty(name='Custom gaps (m)', description='Optional: one repeated gap or count minus one comma-separated gaps in metres')
    extension: FloatProperty(name='End extension', default=2, min=0.001, subtype='DISTANCE')
    rotation: FloatProperty(name='Rotation', default=0, subtype='ANGLE')
    at_cursor: BoolProperty(name='Origin at 3D cursor XY', default=True)
    elevation: FloatProperty(name='Elevation offset', default=0, subtype='DISTANCE', description='Offset above the default spatial container')
    group: EnumProperty(name='Axis family', items=[('U','U · Letters','Horizontal local grid direction'),('V','V · Numbers','Vertical local grid direction')])
    axis_tag: StringProperty(name='New axis label', description='Leave empty for the next unused letter or number')
    snap: FloatProperty(name='Snap step', default=0.1, min=0.001, subtype='DISTANCE', description='Hold Ctrl while drawing to snap in the grid local plane')
    offset: FloatProperty(name='Offset distance', default=6, subtype='DISTANCE', description='Signed left-side offset along the selected axis direction')
    bubbles: BoolProperty(name='Grid bubbles', default=True)
    dashed: BoolProperty(name='Dashed overlay', default=True)
    bubble_radius: IntProperty(name='Bubble size', default=15, min=8, max=36)
    advanced: BoolProperty(name='Unequal spacing / labels', default=False)


class IfcTransaction:
    transaction_key = ''
    transaction_data = None

    @classmethod
    def poll(cls, context):
        return ready(context)

    def execute(self, context):
        # Validate before beginning an IFC transaction: cancellation must not
        # advance Bonsai's history or leave partially created IFC entities.
        try:
            self.validate(context)
        except ValueError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        from bonsai.bim.ifc import IfcStore
        return IfcStore.execute_ifc_operator(self, context)


class IFCGRID_OT_create(IfcTransaction, bpy.types.Operator):
    bl_idname = 'ifcgrid.create'
    bl_label = 'Create IFC Grid'
    bl_description = 'Create a rectangular IFC grid at the cursor on the default spatial container'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360, confirm_text='Create Grid')

    def draw(self, context):
        draw_settings(self.layout, context)

    def validate(self, context):
        s = context.scene.ifc_grid_toolbar
        if not s.name.strip():
            raise ValueError('Enter a grid name')
        if not api().Root.get_default_container():
            raise ValueError('Set a default spatial container in Bonsai before creating a grid')
        self.axes = cartesian_axes(s.u_count, s.v_count, s.u_spacing, s.v_spacing, s.extension, s.u_gaps, s.v_gaps, s.u_start, s.v_start)
        for axes in self.axes.values():
            for axis in axes:
                for key in ('start', 'end'):
                    if not all(math.isfinite(v) for v in Vector(axis[key])):
                        raise ValueError('Grid dimensions exceed Blender coordinate limits')
        if not math.isfinite(s.rotation) or not math.isfinite(s.elevation):
            raise ValueError('Rotation and elevation must be finite')

    def _execute(self, context):
        import bonsai.core.root
        import bonsai.core.geometry
        tool = api()
        s = context.scene.ifc_grid_toolbar
        obj = bpy.data.objects.new(s.name.strip(), None)
        origin = context.scene.cursor.location.copy() if s.at_cursor else Vector((0,0,0))
        origin.z = tool.Root.get_default_container_elevation() + s.elevation
        obj.matrix_world = Matrix.Translation(origin) @ Matrix.Rotation(s.rotation, 4, 'Z')
        grid = bonsai.core.root.assign_class(tool.Ifc, tool.Collector, tool.Root, obj=obj, ifc_class='IfcGrid', should_add_representation=False)
        if hasattr(grid, 'PredefinedType'):
            grid.PredefinedType = 'RECTANGULAR'
        bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj)
        for group, axes in self.axes.items():
            for axis in axes:
                add_axis(context, grid, group, axis['tag'], axis['start'], axis['end'])
        select_objects(context, [obj])
        refresh()
        self.report({'INFO'}, f'Created {s.u_count + s.v_count} IFC grid axes. Save your IFC to keep the changes.')
        return {'FINISHED'}


class IFCGRID_OT_add_axis(IfcTransaction, bpy.types.Operator):
    bl_idname = 'ifcgrid.add_axis'
    bl_label = 'Add IFC Grid Line'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    grid_id: IntProperty()
    group: EnumProperty(items=[('U','U',''),('V','V','')])
    tag: StringProperty()
    start: FloatVectorProperty(size=3)
    end: FloatVectorProperty(size=3)

    def validate(self, context):
        try:
            grid = api().Ifc.get().by_id(self.grid_id)
        except RuntimeError:
            raise ValueError('The target grid is no longer available')
        if not grid or not grid.is_a('IfcGrid') or not api().Ifc.get_object(grid):
            raise ValueError('Select a loaded IFC grid')
        if not grid.UAxes or not grid.VAxes:
            raise ValueError('The target grid must contain both U and V axes')
        self.tag = validate_tag(grid, self.tag or next_tag(grid, self.group))
        if not all(math.isfinite(v) for v in (*self.start, *self.end)) or (Vector(self.end)-Vector(self.start)).length < 1e-4:
            raise ValueError('Pick two distinct finite points')
        if abs(self.start[2]) > 1e-4 or abs(self.end[2]) > 1e-4:
            raise ValueError('Grid line endpoints must lie in the grid plane')

    def _execute(self, context):
        grid = api().Ifc.get().by_id(self.grid_id)
        obj = add_axis(context, grid, self.group, self.tag, self.start, self.end)
        # Arbitrary user-drawn line orientations may no longer be rectangular.
        if hasattr(grid, 'PredefinedType'):
            grid.PredefinedType = 'NOTDEFINED'
        select_objects(context, [obj])
        context.scene.ifc_grid_toolbar.axis_tag = ''
        refresh()
        return {'FINISHED'}


class IFCGRID_OT_draw(bpy.types.Operator):
    bl_idname = 'ifcgrid.draw'
    bl_label = 'Draw Grid Line'
    bl_description = 'Select an IFC grid or axis first. Click two points; Shift constrains direction, Ctrl snaps; Esc cancels'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ready(context) and bool(active_grid(context)) and context.area and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        global _preview
        grid = active_grid(context)
        self.matrix = api().Ifc.get_object(grid).matrix_world.copy()
        normal = self.matrix.to_3x3() @ Vector((0,0,1))
        view_normal = context.space_data.region_3d.view_rotation @ Vector((0,0,1))
        if context.space_data.region_3d.view_perspective != 'ORTHO' or abs(normal.dot(view_normal)) < .999:
            context.space_data.region_3d.view_rotation = self.matrix.to_quaternion()
            context.space_data.region_3d.view_perspective = 'ORTHO'
            context.area.tag_redraw()
        self.grid_id = grid.id()
        self.first = None
        self.point = None
        self.area = context.area
        self.region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self.group = context.scene.ifc_grid_toolbar.group
        self.tag = context.scene.ifc_grid_toolbar.axis_tag or next_tag(grid, self.group)
        try:
            validate_tag(grid, self.tag)
        except ValueError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        _preview = None
        context.window.cursor_modal_set('CROSSHAIR')
        context.area.header_text_set(f'Grid {self.tag}: click start and end · Shift: constrain · Ctrl: snap · Esc: cancel')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cleanup(self, context):
        global _preview
        _preview = None
        self.area.header_text_set(None)
        self.area.tag_redraw()
        context.window.cursor_modal_restore()

    def modal(self, context, event):
        global _preview
        if event.type in {'ESC', 'RIGHTMOUSE'} or not ready(context):
            self.cleanup(context)
            return {'CANCELLED'}
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type in {'MOUSEMOVE','LEFTMOUSE'}:
            from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
            xy = (event.mouse_x-self.region.x, event.mouse_y-self.region.y)
            if not (0 <= xy[0] <= self.region.width and 0 <= xy[1] <= self.region.height):
                return {'RUNNING_MODAL'}
            rv = self.area.spaces.active.region_3d
            ray = region_2d_to_vector_3d(self.region, rv, xy)
            origin = region_2d_to_origin_3d(self.region, rv, xy)
            inv = self.matrix.inverted()
            local_origin = inv @ origin
            local_ray = inv.to_3x3() @ ray
            if abs(local_ray.z) < 1e-8:
                return {'RUNNING_MODAL'}
            self.point = local_origin + local_ray * (-local_origin.z/local_ray.z)
            self.point.z = 0
            if event.ctrl:
                step = context.scene.ifc_grid_toolbar.snap
                self.point.x = round(self.point.x / step) * step
                self.point.y = round(self.point.y / step) * step
            if self.first is not None and event.shift:
                if self.group == 'U': self.point.y = self.first.y
                else: self.point.x = self.first.x
            if self.first is not None:
                _preview = (self.matrix @ self.first, self.matrix @ self.point, self.tag, self.area.as_pointer())
            self.area.tag_redraw()
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                if self.first is None:
                    self.first = self.point.copy()
                elif (self.point-self.first).length >= 1e-4:
                    self.cleanup(context)
                    return bpy.ops.ifcgrid.add_axis('EXEC_DEFAULT', grid_id=self.grid_id, group=self.group, tag=self.tag, start=self.first, end=self.point)
        return {'RUNNING_MODAL'}


class IFCGRID_OT_offset(IfcTransaction, bpy.types.Operator):
    bl_idname = 'ifcgrid.offset'
    bl_label = 'Offset Selected Axis'
    bl_description = 'Create a parallel IFC axis at the signed offset distance'
    bl_options = {'REGISTER','UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(active_axis(context))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300, confirm_text='Offset')

    def draw(self, context):
        self.layout.prop(context.scene.ifc_grid_toolbar, 'offset')
        self.layout.prop(context.scene.ifc_grid_toolbar, 'axis_tag', text='Label (optional)')

    def validate(self, context):
        axis = active_axis(context)
        obj = context.active_object
        if axis.PartOfW:
            raise ValueError('Offset supports U and V grid axes')
        if obj.type != 'MESH' or len(obj.data.vertices) != 2 or len(obj.data.edges) != 1:
            raise ValueError('Offset supports a straight grid axis with two endpoints')
        grid = active_grid(context)
        self.tag = validate_tag(grid, context.scene.ifc_grid_toolbar.axis_tag or next_tag(grid, 'U' if axis.PartOfU else 'V'))
        matrix = api().Ifc.get_object(grid).matrix_world.inverted() @ obj.matrix_world
        self.points = [matrix @ v.co for v in obj.data.vertices]
        if any(abs(p.z) > 1e-4 for p in self.points):
            raise ValueError('The selected axis is outside its grid plane')
        delta = self.points[1]-self.points[0]
        if delta.length < 1e-4:
            raise ValueError('Cannot offset a zero length axis')
        distance = context.scene.ifc_grid_toolbar.offset
        if not math.isfinite(distance) or abs(distance) < 1e-6:
            raise ValueError('Offset distance must be finite and nonzero')
        normal = Vector((-delta.y,delta.x,0)).normalized()*distance
        self.points = [p+normal for p in self.points]
        if not all(math.isfinite(v) for p in self.points for v in p):
            raise ValueError('Offset exceeds Blender coordinate limits')

    def _execute(self, context):
        axis = active_axis(context)
        obj = add_axis(context, active_grid(context), 'U' if axis.PartOfU else 'V', self.tag, *self.points)
        select_objects(context,[obj])
        context.scene.ifc_grid_toolbar.axis_tag = ''
        refresh()
        return {'FINISHED'}


class IFCGRID_OT_rename(IfcTransaction, bpy.types.Operator):
    bl_idname = 'ifcgrid.rename'
    bl_label = 'Rename Selected Axis'
    bl_options = {'REGISTER','UNDO'}
    tag: StringProperty(name='Axis label')

    @classmethod
    def poll(cls, context):
        return bool(active_axis(context))

    def invoke(self, context, event):
        self.tag = active_axis(context).AxisTag or ''
        return context.window_manager.invoke_props_dialog(self)

    def validate(self, context):
        self.tag = validate_tag(active_grid(context), self.tag, active_axis(context))

    def _execute(self, context):
        active_axis(context).AxisTag = self.tag
        context.active_object.name = 'IfcGridAxis/' + self.tag
        refresh()
        return {'FINISHED'}


def axis_geometry(axis_id):
    """Resolve editable straight geometry in grid-local metres."""
    tool = api()
    try:
        axis = tool.Ifc.get().by_id(axis_id)
    except (RuntimeError, AttributeError):
        raise ValueError('This grid axis is no longer available')
    if not axis or not axis.is_a('IfcGridAxis'):
        raise ValueError('Select a straight IFC grid axis')
    obj = tool.Ifc.get_object(axis)
    if not obj or obj.type != 'MESH' or len(obj.data.vertices) != 2 or len(obj.data.edges) != 1:
        raise ValueError('Stretch supports a straight axis with two endpoints')
    if tool.Geometry.is_locked(axis):
        raise ValueError('Unlock grids in Bonsai before stretching')
    if obj.library and not obj.override_library:
        raise ValueError('This linked axis is read-only')
    owners = axis.PartOfU or axis.PartOfV or axis.PartOfW
    grid = owners[0] if owners else None
    grid_obj = tool.Ifc.get_object(grid) if grid else None
    if not grid_obj:
        raise ValueError('The owning grid must be loaded')
    if abs(obj.matrix_world.determinant()) < 1e-12 or abs(grid_obj.matrix_world.determinant()) < 1e-12:
        raise ValueError('The axis has an invalid object scale')
    matrix = grid_obj.matrix_world.inverted() @ obj.matrix_world
    points = [matrix @ vertex.co for vertex in obj.data.vertices]
    if not all(math.isfinite(v) for point in points for v in point):
        raise ValueError('The grid has invalid coordinates')
    if any(abs(point.z) > 1e-4 for point in points):
        raise ValueError('The axis is outside its grid plane')
    if (points[1] - points[0]).length < 1e-4:
        raise ValueError('Cannot stretch a zero-length axis')
    return axis, obj, grid_obj, points


class IFCGRID_OT_set_endpoint(IfcTransaction, bpy.types.Operator):
    bl_idname = 'ifcgrid.set_endpoint'
    bl_label = 'Stretch Grid End'
    bl_description = 'Change only one endpoint and update the existing IFC axis curve'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    axis_id: IntProperty()
    endpoint: IntProperty(min=0, max=1)
    point: FloatVectorProperty(size=3, subtype='XYZ')

    def validate(self, context):
        axis, obj, grid_obj, points = axis_geometry(self.axis_id)
        target = Vector(self.point)
        if not all(math.isfinite(v) for v in target):
            raise ValueError('The endpoint must have finite coordinates')
        fixed = points[1 - self.endpoint]
        direction = (points[self.endpoint] - fixed).normalized()
        distance = (target - fixed).dot(direction)
        if distance < 0.001:
            raise ValueError('Keep at least 1 mm between endpoints; do not cross the fixed end')
        projected = fixed + direction * distance
        if abs(target.z) > 1e-4 or (target - projected).length > max(1e-4, distance * 1e-6):
            raise ValueError('Stretch along the existing grid line')
        self.new_co = obj.matrix_world.inverted() @ grid_obj.matrix_world @ target
        if not all(math.isfinite(v) for v in self.new_co):
            raise ValueError('The endpoint exceeds Blender coordinate limits')

    def _execute(self, context):
        tool = api()
        axis, obj, grid_obj, points = axis_geometry(self.axis_id)
        if obj.data.users > 1 or obj.data.library:
            obj.data = obj.data.copy()
        obj.data.vertices[self.endpoint].co = self.new_co
        obj.data.update()
        tool.Model.create_axis_curve(obj, axis)
        tool.Ifc.finish_edit(obj)
        props = tool.Geometry.get_mesh_props(obj.data)
        props.mesh_checksum = tool.Geometry.get_mesh_checksum(obj.data)
        refresh()
        return {'FINISHED'}


class IFCGRID_OT_stretch(bpy.types.Operator):
    bl_idname = 'ifcgrid.stretch'
    bl_label = 'Stretch Grid End'
    bl_description = 'Drag this endpoint along the grid line. The opposite end stays fixed. Ctrl snaps length; Esc cancels'
    bl_options = {'UNDO', 'INTERNAL', 'BLOCKING'}
    axis_id: IntProperty()
    endpoint: IntProperty(min=0, max=1)

    @classmethod
    def poll(cls, context):
        return ready(context) and context.area and context.area.type == 'VIEW_3D'

    def mouse_distance(self, xy):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        from mathutils.geometry import intersect_line_line
        rv = self.area.spaces.active.region_3d
        origin = region_2d_to_origin_3d(self.region, rv, xy)
        ray = region_2d_to_vector_3d(self.region, rv, xy)
        result = intersect_line_line(self.fixed_world, self.fixed_world + self.direction_world, origin, origin + ray)
        if result is None:
            return None
        return (result[0] - self.fixed_world).dot(self.direction_world)

    def invoke(self, context, event):
        global _stretch_preview
        try:
            axis, obj, grid_obj, points = axis_geometry(self.axis_id)
        except ValueError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        self.area = context.area
        self.region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self.matrix = grid_obj.matrix_world.copy()
        self.fixed = points[1 - self.endpoint].copy()
        self.original = points[self.endpoint].copy()
        self.direction = (self.original - self.fixed).normalized()
        self.fixed_world = self.matrix @ self.fixed
        self.original_world = self.matrix @ self.original
        self.direction_world = (self.original_world - self.fixed_world).normalized()
        self.world_length = (self.original_world - self.fixed_world).length
        self.local_length = (self.original - self.fixed).length
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        a = location_3d_to_region_2d(self.region, self.area.spaces.active.region_3d, self.fixed_world)
        b = location_3d_to_region_2d(self.region, self.area.spaces.active.region_3d, self.original_world)
        if a is None or b is None or (a-b).length < 8:
            self.report({'WARNING'}, 'View the grid from the side or use Top Plan to stretch')
            return {'CANCELLED'}
        self.mouse_origin = self.mouse_distance((event.mouse_x-self.region.x, event.mouse_y-self.region.y))
        if self.mouse_origin is None:
            self.report({'WARNING'}, 'Use Top Plan to stretch this grid end')
            return {'CANCELLED'}
        self.point = self.original.copy()
        self.tag = axis.AxisTag or '?'
        self.is_drag = event.value != 'RELEASE'
        _stretch_preview = None
        context.window.cursor_modal_set('SCROLL_XY')
        self.area.header_text_set(f'Stretch {self.tag} · Drag end · Ctrl: snap length · Esc: cancel')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cleanup(self, context):
        global _stretch_preview
        _stretch_preview = None
        self.area.header_text_set(None)
        self.area.tag_redraw()
        context.window.cursor_modal_restore()

    def modal(self, context, event):
        global _stretch_preview
        if event.type in {'ESC', 'RIGHTMOUSE', 'WINDOW_DEACTIVATE'} or not ready(context):
            self.cleanup(context)
            return {'CANCELLED'}
        if event.type in {'MOUSEMOVE', 'LEFTMOUSE'}:
            distance = self.mouse_distance((event.mouse_x-self.region.x,event.mouse_y-self.region.y))
            if distance is not None:
                length = self.local_length + (distance-self.mouse_origin) * self.local_length/self.world_length
                if event.ctrl:
                    step = context.scene.ifc_grid_toolbar.snap
                    length = round(length / step) * step
                length = max(0.001, length)
                self.point = self.fixed + self.direction * length
                world = [self.matrix @ self.fixed, self.matrix @ self.point]
                if self.endpoint == 0:
                    world.reverse()
                _stretch_preview = {'axis_id':self.axis_id, 'points':world, 'area':self.area.as_pointer(), 'tag':self.tag}
                self.area.tag_redraw()
            if event.type == 'LEFTMOUSE' and ((self.is_drag and event.value == 'RELEASE') or (not self.is_drag and event.value == 'PRESS')):
                self.cleanup(context)
                if (self.point-self.original).length < 1e-5:
                    return {'CANCELLED'}
                return bpy.ops.ifcgrid.set_endpoint('EXEC_DEFAULT', axis_id=self.axis_id, endpoint=self.endpoint, point=self.point)
        return {'RUNNING_MODAL'}


class IFCGRID_GGT_endpoints(bpy.types.GizmoGroup):
    bl_idname = 'IFCGRID_GGT_endpoints'
    bl_label = 'Stretch IFC Grid Ends'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT', 'SCALE'}

    @classmethod
    def poll(cls, context):
        axis = active_axis(context)
        obj = context.active_object
        if not axis or not obj.select_get() or not context.space_data.overlay.show_overlays:
            return False
        try:
            axis_geometry(axis.id())
            return obj.visible_get(view_layer=context.view_layer, viewport=context.space_data)
        except (ValueError, RuntimeError, ReferenceError):
            return False

    def setup(self, context):
        self.handles = []
        for endpoint in (0, 1):
            handle = self.gizmos.new('GIZMO_GT_button_2d')
            handle.draw_options = {'BACKDROP', 'OUTLINE'}
            handle.backdrop_fill_alpha = .04
            handle.color = (1.0, 0.62, 0.15)
            handle.alpha = .85
            handle.color_highlight = (1.0, 0.9, 0.3)
            handle.alpha_highlight = 1.0
            handle.line_width = 2.0
            handle.show_drag = True
            handle.use_draw_modal = True
            op = handle.target_set_operator('ifcgrid.stretch')
            op.endpoint = endpoint
            self.handles.append((handle, op))

    def draw_prepare(self, context):
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        axis = active_axis(context)
        obj = context.active_object
        if not axis or not obj or obj.type != 'MESH':
            for handle, _ in self.handles: handle.hide = True
            return
        for index, (handle, op) in enumerate(self.handles):
            world = obj.matrix_world @ obj.data.vertices[index].co
            if _stretch_preview and _stretch_preview['axis_id'] == axis.id() and _stretch_preview['area'] == context.area.as_pointer():
                world = _stretch_preview['points'][index]
            point = location_3d_to_region_2d(context.region, context.region_data, world)
            handle.hide = point is None or not (0 <= point.x < context.region.width and 0 <= point.y < context.region.height)
            if point is not None:
                handle.matrix_basis = Matrix.Translation((point.x, point.y, 0))
                handle.scale_basis = max(context.scene.ifc_grid_toolbar.bubble_radius + 4, len(axis.AxisTag or '?') * 4 + 9)
            op.axis_id = axis.id()


class IFCGRID_OT_plan(bpy.types.Operator):
    bl_idname = 'ifcgrid.plan'
    bl_label = 'Top Plan'
    bl_description = 'Align to the selected grid plane; otherwise use global top view'

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D' and context.space_data.region_3d is not None

    def execute(self, context):
        grid = active_grid(context)
        rv = context.space_data.region_3d
        rv.view_rotation = api().Ifc.get_object(grid).matrix_world.to_quaternion() if grid else Matrix.Identity(3).to_quaternion()
        rv.view_perspective = 'ORTHO'
        context.area.tag_redraw()
        return {'FINISHED'}


class IFCGRID_OT_unlock(bpy.types.Operator):
    bl_idname = 'ifcgrid.unlock'
    bl_label = 'Unlock Grids'
    bl_description = 'Turn off Bonsai grid editing locks so you can drag axis endpoints'

    @classmethod
    def poll(cls, context):
        return ready(context)

    def execute(self, context):
        api().Spatial.get_grid_props().is_locked = False
        for area in context.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()
        return {'FINISHED'}


def draw_settings(layout, context):
    s = context.scene.ifc_grid_toolbar
    if not ready(context):
        layout.label(text='Open or create an IFC project in Bonsai.', icon='INFO')
        return
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(s,'name')
    for prefix, title in [('u','Horizontal · letters'),('v','Vertical · numbers')]:
        box = layout.box()
        box.label(text=title)
        box.prop(s,prefix+'_count')
        box.prop(s,prefix+'_spacing')
        if s.advanced:
            box.prop(s,prefix+'_start')
            box.prop(s,prefix+'_gaps')
    row = layout.row()
    row.use_property_split = False
    row.prop(s,'advanced',text='More settings',icon='TRIA_DOWN' if s.advanced else 'TRIA_RIGHT',emboss=False)
    if s.advanced:
        layout.prop(s,'extension')
        layout.prop(s,'rotation')
        layout.prop(s,'at_cursor')
        layout.prop(s,'elevation')
    container = api().Root.get_default_container()
    layout.label(text='Container: '+((container.Name or container.is_a()) if container else 'Set a default in Bonsai'), icon='OUTLINER_COLLECTION')


def draw_controls(layout, context):
    layout.use_property_split = False
    layout.use_property_decorate = False
    if not ready(context):
        layout.label(text='Open an IFC project in Bonsai.', icon='INFO')
        return
    grid, axis = active_grid(context), active_axis(context)
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator_context = 'INVOKE_REGION_WIN'
    row.operator('ifcgrid.create', text='New Grid', icon='MESH_GRID')
    row.operator('ifcgrid.draw', text='Draw Line', icon='GREASEPENCIL')
    row.menu('IFCGRID_MT_more', text='', icon='DOWNARROW_HLT')
    if grid:
        layout.label(text=(grid.Name or 'Grid') + ((' / ' + (axis.AxisTag or '?')) if axis else ''), icon='CURVE_PATH')
    else:
        layout.label(text='Select a grid to draw more lines.', icon='INFO')
    if axis:
        if api().Geometry.is_locked(axis):
            layout.operator('ifcgrid.unlock', text='Unlock Grids to Stretch', icon='UNLOCKED')
        else:
            layout.label(text='Drag either end bubble to stretch.')
            layout.label(text='Ctrl: snap length · Esc: cancel')


class IFCGRID_MT_more(bpy.types.Menu):
    bl_label = 'Grid options'
    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'INVOKE_REGION_WIN'
        layout.operator('ifcgrid.offset', text='Offset Axis', icon='DUPLICATE')
        layout.operator('ifcgrid.rename', text='Rename Axis', icon='GREASEPENCIL')
        layout.operator('ifcgrid.plan', icon='VIEW_ORTHO')
        layout.separator()
        s = context.scene.ifc_grid_toolbar
        layout.prop(s, 'group')
        layout.prop(s, 'axis_tag', text='Next label')
        layout.prop(s, 'snap')
        layout.separator()
        layout.prop(s, 'bubbles')
        layout.prop(s, 'dashed')
        layout.prop(s, 'bubble_radius')
        layout.prop(api().Spatial.get_grid_props(), 'is_locked', text='Lock grids')


class IFCGRID_OT_popup(bpy.types.Operator):
    bl_idname = 'ifcgrid.popup'
    bl_label = 'IFC Grid'
    bl_description = 'Create or draw grids. Select an axis and drag either end bubble to stretch it'
    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=320)
    def draw(self,context):
        draw_controls(self.layout,context)
    def execute(self,context):
        return {'FINISHED'}


class IFCGRID_PT_panel(bpy.types.Panel):
    bl_label = 'IFC Grid'
    bl_idname = 'IFCGRID_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'IFC Grid'
    def draw(self, context):
        draw_controls(self.layout, context)


def overlay():
    context = bpy.context
    if not context.area or context.area.type != 'VIEW_3D' or not ready(context): return
    space = context.space_data
    if not space.overlay.show_overlays: return
    s = context.scene.ifc_grid_toolbar
    if not s.bubbles and not s.dashed and not _preview and not _stretch_preview: return
    import gpu, blf
    from gpu_extras.batch import batch_for_shader
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    region, rv = context.region, context.region_data
    if rv is None: return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    def stroke(points,color,kind='LINE_STRIP'):
        if len(points)<2: return
        shader.bind()
        shader.uniform_float('color',color)
        batch_for_shader(shader,kind,{'pos':points}).draw(shader)
    def bubble(point, text, color):
        r = max(s.bubble_radius, len(text)*4+5)
        circle = [(point.x+math.cos(i*math.tau/40)*r,point.y+math.sin(i*math.tau/40)*r) for i in range(41)]
        disk = []
        for i in range(40): disk.extend([(point.x,point.y),circle[i],circle[i+1]])
        stroke(disk,(.045,.065,.08,.98),'TRIS')
        stroke(circle,color)
        blf.size(0,13)
        w,h = blf.dimensions(0,text)
        blf.position(0,point.x-w/2,point.y-h/2,0)
        blf.color(0,*color)
        blf.draw(0,text)
    def line(start,end,tag,color,force=False):
        a = location_3d_to_region_2d(region,rv,start)
        b = location_3d_to_region_2d(region,rv,end)
        if a is None or b is None: return
        length=(b-a).length
        if length < 1: return
        if s.dashed or force:
            d=(b-a)/length
            vertices=[]
            # Bound work when viewing very long grids at extreme zoom.
            for i in range(min(2000,int(length/16)+1)):
                x=i*16
                vertices.extend([a+d*x,a+d*min(x+9,length)])
            stroke(vertices,color,'LINES')
        if s.bubbles or force:
            if -50<a.x<region.width+50 and -50<a.y<region.height+50: bubble(a,tag,color)
            if -50<b.x<region.width+50 and -50<b.y<region.height+50: bubble(b,tag,color)
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(1.5)
    try:
        tool=api()
        for grid in tool.Ifc.get().by_type('IfcGrid'):
            for axis in all_axes(grid):
                obj=tool.Ifc.get_object(axis)
                if not obj or obj.type!='MESH' or len(obj.data.vertices)!=2: continue
                if not obj.visible_get(view_layer=context.view_layer,viewport=space): continue
                color=(1,.65,.22,1) if obj.select_get() else (.55,.82,1,1)
                line(*(obj.matrix_world@v.co for v in obj.data.vertices),axis.AxisTag or '?',color)
        if _preview and _preview[3]==context.area.as_pointer():
            line(*_preview[:3],(1,.75,.2,1),True)
        if _stretch_preview and _stretch_preview['area']==context.area.as_pointer():
            line(*_stretch_preview['points'],_stretch_preview['tag'],(1,.75,.2,1),True)
    finally:
        gpu.state.line_width_set(1)
        gpu.state.blend_set('NONE')


def install_overlay():
    global _handle
    if bpy.app.background: return
    if _handle:
        bpy.types.SpaceView3D.draw_handler_remove(_handle,'WINDOW')
    _handle=bpy.types.SpaceView3D.draw_handler_add(overlay,(),'WINDOW','POST_PIXEL')


def toolbar(self,context):
    if context.mode!='OBJECT': return
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
    generator,show_text=ToolSelectPanelHelper._layout_generator_detect_from_region(self.layout,context.region,1.75)
    generator.send(None)
    col=generator.send(False)
    col.operator_context='INVOKE_REGION_WIN'
    col.operator('ifcgrid.popup',text='IFC Grid' if show_text else '',icon='MESH_GRID')
    generator.send(None)
    self.layout.separator()


def menu(self,context):
    self.layout.operator('ifcgrid.popup',text='IFC Grid',icon='MESH_GRID')


_classes=(IFCGRID_PG_settings,IFCGRID_OT_create,IFCGRID_OT_add_axis,IFCGRID_OT_draw,IFCGRID_OT_offset,IFCGRID_OT_rename,IFCGRID_OT_set_endpoint,IFCGRID_OT_stretch,IFCGRID_GGT_endpoints,IFCGRID_OT_plan,IFCGRID_OT_unlock,IFCGRID_MT_more,IFCGRID_OT_popup,IFCGRID_PT_panel)


def register():
    for cls in _classes: bpy.utils.register_class(cls)
    bpy.types.Scene.ifc_grid_toolbar=PointerProperty(type=IFCGRID_PG_settings)
    bpy.types.VIEW3D_PT_tools_active.prepend(toolbar)
    bpy.types.VIEW3D_MT_add.append(menu)
    install_overlay()


def unregister():
    global _handle,_preview,_stretch_preview
    _preview=None
    _stretch_preview=None
    if _handle:
        bpy.types.SpaceView3D.draw_handler_remove(_handle,'WINDOW')
        _handle=None
    bpy.types.VIEW3D_PT_tools_active.remove(toolbar)
    bpy.types.VIEW3D_MT_add.remove(menu)
    del bpy.types.Scene.ifc_grid_toolbar
    for cls in reversed(_classes): bpy.utils.unregister_class(cls)
