# SPDX-License-Identifier: GPL-3.0-or-later
"""Storey workplanes and native locked plan views for Bonsai."""
bl_info = {
    "name": "Bonsai Storey Toolbar", "author": "Codex", "version": (1, 2, 0),
    "blender": (4, 2, 0), "category": "3D View",
    "location": "3D View > Tool Header and Sidebar > IFC Storeys",
    "description": "Select an IFC storey, set its workplane, and switch between locked plan and 3D",
}

import math
import sys
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from mathutils import Euler, Matrix, Vector
from . import view_range

range_bounds = view_range.range_bounds
make_range_planes = view_range.make_range_planes
apply_range = view_range.apply_range
release_range = view_range.release_range

_views = {}
_files = {}


def api():
    import bonsai.tool as tool
    return tool


def ifc_file():
    try:
        return api().Ifc.get()
    except (ImportError, AttributeError):
        return None


def selected_storey(scene):
    file = ifc_file()
    guid = scene.ifc_storey_toolbar.selected_guid
    if file and guid:
        try:
            entity = file.by_guid(guid)
            return entity if entity.is_a("IfcBuildingStorey") else None
        except (RuntimeError, ValueError):
            pass
    return None


def object_placement(obj, visited=None):
    """Hidden spatial objects may have unevaluated/stale matrix_world values.

    Compose their live transform channels, including parent inverse, instead.
    Bonsai already expresses these channels in Blender's local SI coordinates.
    """
    visited = set() if visited is None else visited
    if obj.as_pointer() in visited:
        raise ValueError('Cyclic storey parent placement')
    visited.add(obj.as_pointer())
    basis = obj.matrix_basis.copy()
    if obj.parent:
        if obj.parent_type != 'OBJECT':
            raise ValueError('Storey placement requires ordinary object parenting')
        return object_placement(obj.parent, visited) @ obj.matrix_parent_inverse @ basis
    return basis


def storey_frame(entity):
    """Resolve the actual storey transform even if its collection is excluded."""
    obj = api().Ifc.get_object(entity)
    if obj is None:
        raise ValueError("Load this storey in Bonsai before selecting its workplane")
    matrix = object_placement(obj)
    if not all(math.isfinite(v) for row in matrix for v in row):
        raise ValueError("The storey has an invalid placement")
    if abs(matrix.to_3x3().determinant()) < 1e-10:
        raise ValueError("The storey placement has a zero scale")
    rotation = matrix.to_quaternion().normalized().to_matrix().to_4x4()
    rotation.translation = matrix.translation
    return rotation


def viewports(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                if not space.region_quadviews:
                    yield area, space, space.region_3d


def save_view(scene, space, region):
    key = region.as_pointer()
    if key not in _views:
        _views[key] = {
            "scene": scene.as_pointer(), "region": region, "space": space,
            "rotation": region.view_rotation.copy(),
            "location": region.view_location.copy(),
            "distance": region.view_distance,
            "perspective": region.view_perspective,
            "lock": region.lock_rotation,
            "side": region.is_orthographic_side_view,
            "view_lock": space.lock_object, "lock_bone": space.lock_bone,
            "lock_cursor": space.lock_cursor,
        }


def lock_view(scene, area, space, region, matrix):
    save_view(scene, space, region)
    space.lock_object = None
    space.lock_cursor = False
    origin = matrix.translation
    normal = matrix.to_3x3().col[2].normalized()
    center = region.view_location.copy()
    region.lock_rotation = False
    region.is_orthographic_side_view = False
    region.view_rotation = matrix.to_quaternion().normalized()
    region.view_perspective = 'ORTHO'
    region.view_location = center - normal * (center - origin).dot(normal)
    region.lock_rotation = True
    if not bpy.app.background:
        region.update()
    area.tag_redraw()


def apply_workplane(context, entity, matrix):
    tool = api()
    tool.Spatial.set_default_container(entity)
    # Reuse the installed toolkit so its visible grid follows the IFC plane.
    toolkit = next((module for name, module in tuple(sys.modules.items())
                    if (name == 'workplane_toolkit' or name.endswith('.workplane_toolkit'))
                    and hasattr(module, 'apply_frame')), None)
    if toolkit and hasattr(context.scene, 'workplane_toolkit'):
        pivot = context.scene.tool_settings.transform_pivot_point
        toolkit.apply_frame(context, matrix, "IFC Storey: " + (entity.Name or "Unnamed"))
        context.scene.tool_settings.transform_pivot_point = pivot
    else:
        context.scene.cursor.matrix = matrix
        context.scene.transform_orientation_slots[0].type = 'CURSOR'
    # Bonsai reads a custom orientation matrix, not Blender's CURSOR mode.
    # use_view avoids creating or selecting any temporary model objects.
    area, space, _ = next(viewports(context.scene))
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with context.temp_override(area=area, region=region):
        bpy.ops.transform.create_orientation(name='IFC Storey Workplane',
                                             use_view=True, use=True, overwrite=True)
    context.scene.transform_orientation_slots[0].custom_orientation.matrix = matrix.to_3x3()
    handler = sys.modules.get('bonsai.bim.handler')
    if handler:
        handler.refresh_ui_data()


def set_plan(context, entity):
    if context.area and context.area.type == 'VIEW_3D' and context.space_data.region_quadviews:
        raise ValueError("Exit Quad View (Ctrl+Alt+Q) before locking a storey plan")
    if not entity or not entity.is_a('IfcBuildingStorey'):
        raise ValueError("Select an IFC building storey first")
    matrix = storey_frame(entity)
    if matrix.to_3x3().col[2].normalized().z < 0.99999:
        raise ValueError("Bonsai storey authoring requires a horizontal level; this storey is tilted")
    targets = list(viewports(context.scene))
    if not targets:
        raise ValueError("Open a 3D viewport before selecting a storey")
    apply_workplane(context, entity, matrix)
    settings = context.scene.ifc_storey_toolbar
    settings.selected_guid = entity.GlobalId
    settings.plan_locked = True
    _files[context.scene.as_pointer()] = ifc_file()
    for area, space, region in targets:
        lock_view(context.scene, area, space, region, matrix)
    apply_range(context.scene)


def release_views(scene=None, restore=False):
    pointer = scene.as_pointer() if scene else None
    for key, saved in list(_views.items()):
        if pointer is not None and saved['scene'] != pointer:
            continue
        try:
            region = saved['region']
            space = saved['space']
            region.lock_rotation = False
            region.is_orthographic_side_view = False
            region.view_rotation = saved['rotation']
            region.view_perspective = saved['perspective'] if restore else 'PERSP'
            if restore:
                region.view_location = saved['location']
                region.view_distance = saved['distance']
                region.is_orthographic_side_view = saved['side']
                region.lock_rotation = saved['lock']
            elif abs((region.view_rotation @ Vector((0, 0, 1))).z) > 0.9999:
                # The first saved view may itself be a flat top plan.
                region.view_rotation = Euler((math.radians(55), 0, math.radians(35))).to_quaternion()
            space.lock_object = saved['view_lock']
            space.lock_bone = saved['lock_bone']
            space.lock_cursor = saved['lock_cursor']
            if not bpy.app.background:
                region.update()
        except (ReferenceError, RuntimeError):
            pass
        _views.pop(key, None)


def set_3d(context):
    context.scene.ifc_storey_toolbar.plan_locked = False
    release_views(context.scene)
    _files.pop(context.scene.as_pointer(), None)
    # Includes a viewport restored from a saved .blend with no runtime backup.
    for area, space, region in viewports(context.scene):
        region.lock_rotation = False
        region.is_orthographic_side_view = False
        region.view_perspective = 'PERSP'
        if abs((region.view_rotation @ Vector((0, 0, 1))).z) > 0.9999:
            region.view_rotation = Euler((math.radians(55), 0, math.radians(35))).to_quaternion()
        area.tag_redraw()


def guard_views():
    """Keep newly opened viewports and external view changes in the chosen plane."""
    view_range.refresh_all()
    for scene in bpy.data.scenes:
        settings = scene.ifc_storey_toolbar
        if not settings.plan_locked:
            continue
        entity = selected_storey(scene)
        file = ifc_file()
        if not entity or (_files.get(scene.as_pointer(), file) is not file):
            settings.plan_locked = False
            release_views(scene, restore=True)
            continue
        try:
            frame = storey_frame(entity)
            q = frame.to_quaternion()
            for area, space, region in viewports(scene):
                wrong_rotation = abs(region.view_rotation.dot(q)) < 0.999999
                if not region.lock_rotation or region.view_perspective != 'ORTHO' or wrong_rotation:
                    lock_view(scene, area, space, region, frame)
        except (ReferenceError, RuntimeError, ValueError):
            settings.plan_locked = False
            release_views(scene, restore=True)
    return 0.2


@persistent
def on_load(_):
    _views.clear()
    _files.clear()
    # Native view locks are saved in .blend files; runtime backups are not.
    # Clear persisted locks first. The timer reacquires valid storey plans,
    # recording an unlocked state so disabling the add-on remains reversible.
    for scene in bpy.data.scenes:
        if scene.ifc_storey_toolbar.plan_locked:
            for area, space, region in viewports(scene):
                region.lock_rotation = False
                area.tag_redraw()


def initialize_saved_views():
    # Add-on registration runs under Blender's restricted data context.
    if not _views:
        on_load(None)
    return None


class IFCSTOREY_PG_settings(bpy.types.PropertyGroup):
    selected_guid: StringProperty(name="Storey GUID")
    plan_locked: BoolProperty(name="Plan Locked", default=False)
    range_enabled: BoolProperty(name='View Range', default=True,
        description='Clip geometry below and above the selected elevation limits', update=view_range.settings_changed)
    range_mode: EnumProperty(name='Range Mode', default='LEVELS',
        items=[('LEVELS', 'Level to Level', 'Limits follow IFC level elevations, with adjustable offsets'),
               ('CUSTOM', 'Custom Elevations', 'Set absolute lower and upper elevations')],
        update=view_range.mode_changed)
    range_lower_guid: StringProperty(update=view_range.settings_changed)
    range_upper_guid: StringProperty(update=view_range.settings_changed)
    range_lower_offset: FloatProperty(name='Lower Offset', default=0, unit='LENGTH', subtype='DISTANCE',
        description='Distance above or below the lower reference level', update=view_range.settings_changed)
    range_upper_offset: FloatProperty(name='Upper Offset', default=0, unit='LENGTH', subtype='DISTANCE',
        description='Distance above or below the upper reference level', update=view_range.settings_changed)
    range_fallback_height: FloatProperty(name='Height Above Level', default=3, min=0.001, unit='LENGTH', subtype='DISTANCE',
        description='Upper height when there is no higher loaded storey', update=view_range.settings_changed)
    range_min: FloatProperty(name='Lower Elevation', default=0, unit='LENGTH', subtype='DISTANCE', update=view_range.settings_changed)
    range_max: FloatProperty(name='Upper Elevation', default=3, unit='LENGTH', subtype='DISTANCE', update=view_range.settings_changed)
    range_status: StringProperty(options={'SKIP_SAVE'})


class IFCSTOREY_OT_select(bpy.types.Operator):
    bl_idname = 'ifcstorey.select_storey'
    bl_label = 'Select Storey Workplane'
    bl_description = 'Set the default IFC container and workplane; lock the viewport in plan'
    guid: StringProperty()

    def execute(self, context):
        try:
            file = ifc_file()
            if not file:
                raise ValueError("Open an IFC project in Bonsai first")
            set_plan(context, file.by_guid(self.guid))
        except (ValueError, RuntimeError) as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class IFCSTOREY_OT_plan(bpy.types.Operator):
    bl_idname = 'ifcstorey.plan_view'
    bl_label = 'Plan Locked'
    bl_description = 'Return to the selected storey workplane and lock plan rotation'

    def execute(self, context):
        try:
            entity = selected_storey(context.scene)
            if not entity and ifc_file():
                entity = api().Root.get_default_container()
            set_plan(context, entity)
        except (ValueError, RuntimeError) as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class IFCSTOREY_OT_3d(bpy.types.Operator):
    bl_idname = 'ifcstorey.view_3d'
    bl_label = '3D View'
    bl_description = 'Unlock navigation and use perspective; keep the chosen storey workplane'

    def execute(self, context):
        set_3d(context)
        return {'FINISHED'}


class IFCSTOREY_MT_storeys(bpy.types.Menu):
    bl_label = 'Select IFC Building Storey'

    def draw(self, context):
        layout = self.layout
        file = ifc_file()
        if not file:
            layout.label(text="Open an IFC project in Bonsai", icon='INFO')
            return
        storeys = file.by_type('IfcBuildingStorey')
        if not storeys:
            layout.label(text="No IFC building storeys in this project", icon='INFO')
            return
        def height(entity):
            obj = api().Ifc.get_object(entity)
            return storey_frame(entity).translation.z if obj else float(entity.Elevation or 0)
        for entity in sorted(storeys, key=lambda e: (height(e), (e.Name or '').casefold(), e.id())):
            row = layout.row()
            loaded = api().Ifc.get_object(entity) is not None
            row.enabled = loaded
            label = entity.Name or f"Storey #{entity.id()}"
            if loaded:
                unit = bpy.utils.units.to_string('METRIC', 'LENGTH', height(entity), precision=3)
                label += "  |  " + unit
            else:
                label += " (not loaded)"
            icon = 'CHECKMARK' if entity.GlobalId == context.scene.ifc_storey_toolbar.selected_guid else 'OUTLINER_COLLECTION'
            row.operator('ifcstorey.select_storey', text=label, icon=icon).guid = entity.GlobalId


def draw_controls(layout, context):
    settings = context.scene.ifc_storey_toolbar
    entity = selected_storey(context.scene)
    name = (entity.Name or 'Unnamed storey') if entity else 'Select Storey'
    row = layout.row(align=True)
    selector = row.row(align=True)
    selector.ui_units_x = 9
    selector.menu('IFCSTOREY_MT_storeys', text=name, icon='OUTLINER_COLLECTION')
    row.operator('ifcstorey.plan_view', text='Plan Locked', icon='LOCKED', depress=settings.plan_locked)
    row.operator('ifcstorey.view_3d', text='3D View', icon='VIEW_PERSPECTIVE', depress=not settings.plan_locked)
    row.popover(panel='IFCSTOREY_PT_view_range', text='View Range', icon='MOD_MASK' if settings.range_enabled else 'HIDE_OFF')


def draw_header(self, context):
    if context.scene and ifc_file():
        draw_controls(self.layout, context)
        self.layout.separator()


class IFCSTOREY_PT_toolbar(bpy.types.Panel):
    bl_label = 'Storey Workplane'
    bl_idname = 'IFCSTOREY_PT_toolbar'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'IFC Storeys'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ifc_storey_toolbar
        entity = selected_storey(context.scene)
        layout.menu('IFCSTOREY_MT_storeys', text=(entity.Name or 'Unnamed storey') if entity else 'Select Storey', icon='OUTLINER_COLLECTION')
        row = layout.row(align=True)
        row.operator('ifcstorey.plan_view', icon='LOCKED', depress=settings.plan_locked)
        row.operator('ifcstorey.view_3d', icon='VIEW_PERSPECTIVE', depress=not settings.plan_locked)
        if entity:
            try:
                elevation = storey_frame(entity).translation.z
                layout.label(text="Elevation: " + bpy.utils.units.to_string('METRIC', 'LENGTH', elevation, precision=3))
            except ValueError as error:
                layout.label(text=str(error), icon='ERROR')
        layout.label(text='Pan and zoom available' if settings.plan_locked else 'Orbit and perspective available', icon='INFO')
        layout.label(text='Storey sets the default IFC container')
        if settings.plan_locked:
            layout.label(text='Use 3D View to unlock rotation')


_classes = (IFCSTOREY_PG_settings, IFCSTOREY_OT_select, IFCSTOREY_OT_plan,
            IFCSTOREY_OT_3d, IFCSTOREY_MT_storeys, IFCSTOREY_PT_toolbar) + view_range.CLASSES


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ifc_storey_toolbar = PointerProperty(type=IFCSTOREY_PG_settings)
    bpy.app.timers.register(initialize_saved_views, first_interval=0.0)
    bpy.types.VIEW3D_HT_tool_header.prepend(draw_header)
    bpy.app.handlers.load_post.append(on_load)
    bpy.app.timers.register(guard_views, first_interval=0.2, persistent=True)
    view_range.register()


def unregister():
    view_range.unregister()
    if bpy.app.timers.is_registered(initialize_saved_views):
        bpy.app.timers.unregister(initialize_saved_views)
    if bpy.app.timers.is_registered(guard_views):
        bpy.app.timers.unregister(guard_views)
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
    release_views(restore=True)
    _files.clear()
    try:
        bpy.types.VIEW3D_HT_tool_header.remove(draw_header)
    except (ValueError, RuntimeError):
        pass
    for scene in bpy.data.scenes:
        scene.ifc_storey_toolbar.plan_locked = False
    del bpy.types.Scene.ifc_storey_toolbar
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()
