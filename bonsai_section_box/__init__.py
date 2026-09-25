# SPDX-License-Identifier: GPL-3.0-or-later
"""Revit-style selection box for Blender / Bonsai. Viewport-only, no IFC edits."""

bl_info = {
    "name": "Bonsai 3D Section Box",
    "author": "Custom BIM Tools",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Left Toolbar | Sidebar > Section | Alt Shift B",
    "description": "Fit a live six-plane section box around selected elements",
    "category": "3D View",
}

import math
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, PointerProperty
from mathutils import Vector

_session = None
_draw_handle = None
_keymaps = []
_updating = False
_saving = False
_bonsai_hook = None
_toolbar_icon = 0
_geometry_types = {"MESH", "CURVE", "SURFACE", "FONT", "META", "VOLUME", "POINTCLOUD"}


def selection_bounds(context):
    """Bounds include evaluated geometry, transforms and selected instancers."""
    selected = list(context.selected_objects)
    if not selected:
        raise ValueError("Select one or more model elements first")
    candidates = {obj.as_pointer(): obj for obj in selected}
    # Spatial containers can have ordinary Blender child objects.
    for obj in selected:
        if obj.type == "EMPTY":
            for child in obj.children_recursive:
                if child.visible_get(view_layer=context.view_layer):
                    candidates[child.as_pointer()] = child
    depsgraph = context.evaluated_depsgraph_get()
    points = []

    def add_bounds(obj, matrix):
        if obj.type not in _geometry_types:
            return
        corners = list(obj.bound_box)
        if all(tuple(corner) == (-1.0, -1.0, -1.0) for corner in corners):
            return
        for corner in corners:
            point = matrix @ Vector(corner)
            if all(math.isfinite(value) for value in point):
                points.append(point)

    for obj in candidates.values():
        evaluated = obj.evaluated_get(depsgraph)
        add_bounds(evaluated, evaluated.matrix_world)
    # Only traverse instances when the selection actually contains an instancer.
    if any(obj.is_instancer for obj in candidates.values()):
        for instance in depsgraph.object_instances:
            if not instance.is_instance or not instance.parent:
                continue
            if instance.parent.original.as_pointer() in candidates:
                add_bounds(instance.object, instance.matrix_world)
    if not points:
        raise ValueError("Selection has no visible geometry; select a model element")
    low = Vector(tuple(min(point[i] for point in points) for i in range(3)))
    high = Vector(tuple(max(point[i] for point in points) for i in range(3)))
    return low, high


def box_planes(low, high):
    return (
        (1.0, 0.0, 0.0, -low.x), (-1.0, 0.0, 0.0, high.x),
        (0.0, 1.0, 0.0, -low.y), (0.0, -1.0, 0.0, high.y),
        (0.0, 0.0, 1.0, -low.z), (0.0, 0.0, -1.0, high.z),
    )


def box_bounds(settings):
    center = Vector(settings.center)
    half = Vector(settings.size) * 0.5 + Vector((settings.padding,) * 3)
    return center - half, center + half


def _bonsai_planes(scene):
    props = getattr(scene, "BIMProjectProperties", None)
    return bool(props and any(item.obj for item in props.clipping_planes))


def _owner_valid():
    if _session is None:
        return False
    try:
        s = _session
        return (
            s["window"] in tuple(bpy.context.window_manager.windows)
            and s["window"].scene == s["scene"]
            and s["area"] in tuple(s["window"].screen.areas)
            and s["area"].type == "VIEW_3D"
            and s["area"].spaces.active == s["space"]
            and not s["space"].region_quadviews
        )
    except (ReferenceError, RuntimeError):
        return False


def _override(s):
    return bpy.context.temp_override(window=s["window"], area=s["area"], region=s["region"])


def _initialize_clip(s):
    # clip_border allocates the native clipbb; setting the RNA flag alone does not.
    if s["space"].shading.type not in {"SOLID", "WIREFRAME"}:
        s["space"].shading.type = "SOLID"
    with _override(s):
        bpy.ops.view3d.clip_border(
            "EXEC_DEFAULT", xmin=0, ymin=0,
            xmax=max(s["region"].width, 1), ymax=max(s["region"].height, 1),
        )


def _free_clip(s):
    # INVOKE with clipping enabled frees clipbb, without opening the border tool.
    if bpy.app.background:
        # Background Blender redirects INVOKE to EXEC (which allocates instead).
        s["rv3d"].use_clip_planes = False
        return
    if s["space"].shading.type not in {"SOLID", "WIREFRAME"}:
        s["space"].shading.type = "SOLID"
    s["rv3d"].use_clip_planes = True
    with _override(s):
        bpy.ops.view3d.clip_border("INVOKE_DEFAULT")
    s["rv3d"].use_clip_planes = False


def _apply(settings=None):
    if not _owner_valid() or _saving:
        return
    s = _session
    settings = settings or s["wm"].bonsai_section_box
    low, high = box_bounds(settings)
    s["rv3d"].clip_planes = box_planes(low, high)
    s["rv3d"].use_box_clip = False
    s["rv3d"].use_clip_planes = True
    s["area"].tag_redraw()


def _settings_changed(self, context):
    if not _updating:
        _apply(self)


def _frame(s, settings):
    low, high = box_bounds(settings)
    radius = max((high - low).length * 0.5, 0.01)
    rv = s["rv3d"]
    rv.view_perspective = "PERSP" if rv.view_perspective == "CAMERA" else rv.view_perspective
    rv.view_location = (low + high) * 0.5
    # Use Blender's actual projection, including viewport aspect and lens zoom.
    if bpy.app.background:
        # RegionView3D.update needs an initialized drawing context.
        rv.view_distance = radius * 4.0
    else:
        rv.update()
        projection = max(abs(rv.window_matrix[0][0]), abs(rv.window_matrix[1][1]), 0.001)
        if rv.view_perspective == "ORTHO":
            rv.view_distance = max(rv.view_distance, 0.01) * radius * projection * 1.2
        else:
            rv.view_distance = radius * math.sqrt(1.0 + projection * projection) * 1.2
    s["space"].clip_start = min(s["space"].clip_start, max(radius * 0.001, 0.0001))
    s["space"].clip_end = max(s["space"].clip_end, rv.view_distance + radius * 4)
    s["area"].tag_redraw()


def clear_section(restore_view=True):
    global _session, _draw_handle
    s = _session
    _session = None
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    if s is None:
        return
    try:
        rv, space, old = s["rv3d"], s["space"], s["previous"]
        try:
            if old["use_clip_planes"]:
                if not rv.use_clip_planes:
                    _initialize_clip(s)
                rv.clip_planes = old["clip_planes"]
                rv.use_clip_planes = True
            elif rv.use_clip_planes:
                _free_clip(s)
        except RuntimeError:
            rv.use_clip_planes = old["use_clip_planes"]
            rv.clip_planes = old["clip_planes"]
        rv.use_box_clip = old["use_box_clip"]
        space.shading.type = old["shading"]
        space.clip_start, space.clip_end = old["clip_start"], old["clip_end"]
        if restore_view:
            rv.view_location = old["view_location"]
            rv.view_rotation = old["view_rotation"]
            rv.view_distance = old["view_distance"]
            rv.view_perspective = old["view_perspective"]
        if s["exited_local_view"] and space.local_view is None:
            layer = s["window"].view_layer
            selected = [obj for obj in layer.objects if obj.select_get()]
            active = layer.objects.active
            members = []
            for obj, member in s["local_members"]:
                try:
                    if member and obj.name in layer.objects:
                        members.append(obj)
                except ReferenceError:
                    pass
            with _override(s):
                try:
                    for obj in selected:
                        obj.select_set(False)
                    for obj in members:
                        obj.select_set(True)
                    if members:
                        layer.objects.active = members[0]
                        bpy.ops.view3d.localview("EXEC_DEFAULT", frame_selected=False)
                    if space.local_view:
                        member_ids = {obj.as_pointer() for obj in members}
                        for obj in layer.objects:
                            obj.local_view_set(space, obj.as_pointer() in member_ids)
                finally:
                    for obj in members:
                        obj.select_set(False)
                    for obj in selected:
                        try:
                            obj.select_set(True)
                        except ReferenceError:
                            pass
                    layer.objects.active = active
        s["area"].tag_redraw()
    except (ReferenceError, RuntimeError, TypeError):
        # The workspace, viewport, or .blend may have been closed by the user.
        pass


def _draw_box():
    if not _owner_valid() or _saving:
        return
    s = _session
    context = bpy.context
    if context.space_data != s["space"]:
        return
    settings = s["wm"].bonsai_section_box
    if not settings.show_box:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    low, high = box_bounds(settings)
    corners = [Vector((x, y, z)) for x in (low.x, high.x)
               for y in (low.y, high.y) for z in (low.z, high.z)]
    edges = [(i, i ^ bit) for i in range(8) for bit in (1, 2, 4) if i < (i ^ bit)]
    coords = []
    for a, b in edges:
        p = location_3d_to_region_2d(context.region, s["rv3d"], corners[a])
        q = location_3d_to_region_2d(context.region, s["rv3d"], corners[b])
        if p is not None and q is not None:
            coords.extend((p, q))
    if not coords:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": coords})
    blend, depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        shader.bind()
        shader.uniform_float("color", (0.12, 0.7, 1.0, 0.9))
        batch.draw(shader)
    finally:
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set(depth)


def _watch():
    if _session is None:
        return None
    if not _owner_valid():
        clear_section(restore_view=False)
        return None
    s = _session
    active = s["window"].view_layer.objects.active
    if active and active.mode != "OBJECT":
        clear_section(restore_view=False)
        return None
    if s["space"].local_view or _bonsai_planes(s["scene"]):
        clear_section(restore_view=False)
        return None
    if not _saving and not s["rv3d"].use_clip_planes:
        clear_section(restore_view=False)
        return None
    # Native section clipping is only supported by Workbench viewport shading.
    if s["space"].shading.type not in {"SOLID", "WIREFRAME"}:
        s["space"].shading.type = "SOLID"
    return 0.2


def _install_bonsai_hook():
    """Keep Bonsai's idle zero-plane refresh from clearing our active box.

    Real Bonsai clipping planes take ownership immediately. No Bonsai files or
    properties are modified, and the original method is restored on disable.
    """
    global _bonsai_hook
    if _bonsai_hook:
        return
    if not hasattr(bpy.types.Scene, "BIMProjectProperties"):
        return
    try:
        from bonsai.bim.module.project.operator import RefreshClippingPlanes
    except (ImportError, AttributeError):
        return
    original = RefreshClippingPlanes.refresh_clipping_planes

    def refresh(operator, context):
        if _session is not None and context.scene == _session["scene"]:
            first = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
            if first == _session["area"]:
                if not _bonsai_planes(context.scene):
                    return {"FINISHED"}
                clear_section(restore_view=False)
        return original(operator, context)

    RefreshClippingPlanes.refresh_clipping_planes = refresh
    _bonsai_hook = (RefreshClippingPlanes, original, refresh)


class SECTIONBOX_PG_settings(bpy.types.PropertyGroup):
    padding: FloatProperty(
        name="Padding", description="Extra space on each side of the box",
        default=0.3, min=0.0, subtype="DISTANCE", unit="LENGTH", update=_settings_changed,
    )
    center: FloatVectorProperty(
        name="Center", size=3, subtype="TRANSLATION", unit="LENGTH", update=_settings_changed,
    )
    size: FloatVectorProperty(
        name="Size", description="Box dimensions before padding", size=3,
        default=(1.0, 1.0, 1.0), min=0.0001, subtype="XYZ", unit="LENGTH", update=_settings_changed,
    )
    show_box: BoolProperty(name="Show Box Outline", default=True, update=_settings_changed)
    auto_frame: BoolProperty(name="Zoom to Box", default=True)


class SECTIONBOX_OT_create(bpy.types.Operator):
    bl_idname = "view3d.bonsai_section_box"
    bl_label = "3D Section Box from Selection"
    bl_description = "Fit a section box around selected elements; show all model geometry inside"

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == "VIEW_3D" and context.mode == "OBJECT"

    def execute(self, context):
        global _session, _draw_handle, _updating
        space = context.space_data
        if space.region_quadviews:
            self.report({"WARNING"}, "Exit Quad View before creating a section box")
            return {"CANCELLED"}
        if _bonsai_planes(context.scene):
            self.report({"WARNING"}, "Clear Bonsai clipping planes before creating a section box")
            return {"CANCELLED"}
        try:
            low, high = selection_bounds(context)
        except ValueError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        settings = context.window_manager.bonsai_section_box
        if _session is not None and _session["space"] != space:
            clear_section()
        if _session is None:
            region = next(r for r in context.area.regions if r.type == "WINDOW")
            rv = space.region_3d
            _session = {
                "window": context.window, "wm": context.window_manager,
                "area": context.area, "region": region,
                "space": space, "rv3d": rv, "scene": context.scene,
                "exited_local_view": bool(space.local_view), "local_members": [],
                "previous": {
                    "use_clip_planes": rv.use_clip_planes,
                    "clip_planes": tuple(tuple(p) for p in rv.clip_planes),
                    "use_box_clip": rv.use_box_clip, "shading": space.shading.type,
                    "clip_start": space.clip_start, "clip_end": space.clip_end,
                    "view_location": rv.view_location.copy(), "view_rotation": rv.view_rotation.copy(),
                    "view_distance": rv.view_distance, "view_perspective": rv.view_perspective,
                },
            }
            try:
                if space.local_view:
                    _session["local_members"] = [(obj, obj.local_view_get(space)) for obj in context.view_layer.objects]
                    with _override(_session):
                        bpy.ops.view3d.localview("EXEC_DEFAULT", frame_selected=False)
                if not rv.use_clip_planes:
                    _initialize_clip(_session)
                if space.shading.type not in {"SOLID", "WIREFRAME"}:
                    space.shading.type = "SOLID"
                _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_box, (), "WINDOW", "POST_PIXEL")
                if not bpy.app.timers.is_registered(_watch):
                    bpy.app.timers.register(_watch, first_interval=0.2)
                _install_bonsai_hook()
            except Exception as error:
                clear_section()
                self.report({"ERROR"}, f"Could not create section box: {error}")
                return {"CANCELLED"}
        _updating = True
        try:
            settings.center = (low + high) * 0.5
            settings.size = tuple(max(high[i] - low[i], 0.0001) for i in range(3))
        finally:
            _updating = False
        _apply(settings)
        if settings.auto_frame:
            _frame(_session, settings)
        self.report({"INFO"}, "Section box active — all visible geometry inside the box is shown")
        return {"FINISHED"}


class SECTIONBOX_OT_clear(bpy.types.Operator):
    bl_idname = "view3d.bonsai_section_box_clear"
    bl_label = "Clear Section Box"
    bl_description = "Remove the section box and restore the original view and clipping"

    @classmethod
    def poll(cls, context):
        return _session is not None

    def execute(self, context):
        clear_section()
        return {"FINISHED"}


class SECTIONBOX_OT_frame(bpy.types.Operator):
    bl_idname = "view3d.bonsai_section_box_frame"
    bl_label = "Frame Section Box"

    @classmethod
    def poll(cls, context):
        return _owner_valid()

    def execute(self, context):
        _frame(_session, context.window_manager.bonsai_section_box)
        return {"FINISHED"}


class SECTIONBOX_PT_panel(bpy.types.Panel):
    bl_label = "3D Section Box"
    bl_idname = "SECTIONBOX_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Section"

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.bonsai_section_box
        row = layout.row()
        row.scale_y = 1.5
        row.operator("view3d.bonsai_section_box", text="3D Section Box", icon="CUBE")
        layout.label(text="Select elements, then click above.")
        layout.prop(settings, "padding")
        layout.prop(settings, "auto_frame")
        if _session is not None:
            box = layout.box()
            box.label(text="Section box active", icon="CHECKMARK")
            box.prop(settings, "center")
            box.prop(settings, "size")
            box.prop(settings, "show_box")
            box.operator("view3d.bonsai_section_box_frame", icon="VIEWZOOM")
            row = layout.row()
            row.scale_y = 1.3
            row.operator("view3d.bonsai_section_box_clear", icon="X")
        layout.separator()
        layout.label(text="Shortcut: Alt Shift B")
        layout.label(text="Viewport only · Solid / Wireframe")


def _view_menu(self, context):
    self.layout.separator()
    self.layout.operator("view3d.bonsai_section_box", icon="CUBE")
    if _session:
        self.layout.operator("view3d.bonsai_section_box_clear", icon="X")


def _header(self, context):
    row = self.layout.row(align=True)
    row.operator("view3d.bonsai_section_box", text="Section Box", icon="CUBE")
    if _session:
        row.operator("view3d.bonsai_section_box_clear", text="", icon="X")


def _create_toolbar_icon():
    """A mint wire cube with an amber cutting plane, using native vector icons."""
    coords, colors = bytearray(), bytearray()

    def triangle(a, b, c, color):
        for point in (a, b, c):
            coords.extend(round(value) for value in point)
            colors.extend(color)

    def quad(a, b, c, d, color):
        triangle(a, b, c, color)
        triangle(a, c, d, color)

    def line(a, b, color, width=7):
        delta = Vector(b) - Vector(a)
        normal = Vector((-delta.y, delta.x)).normalized() * width * 0.5
        quad(Vector(a) + normal, Vector(b) + normal,
             Vector(b) - normal, Vector(a) - normal, color)

    mint = (117, 232, 190, 255)
    light = (211, 255, 239, 255)
    amber = (255, 193, 83, 255)
    bottom, left, right = (128, 27), (48, 71), (208, 71)
    top, upper_left, upper_right = (128, 225), (48, 179), (208, 179)
    middle = (128, 135)
    plane = ((37, 120), (128, 70), (219, 120), (128, 170))
    quad(*plane, (255, 180, 65, 110))
    for a, b in ((bottom, left), (bottom, right), (left, upper_left),
                 (right, upper_right), (upper_left, top), (top, upper_right)):
        line(a, b, mint)
    for a, b in ((middle, upper_left), (middle, upper_right), (middle, bottom)):
        line(a, b, light)
    for i in range(4):
        line(plane[i], plane[(i + 1) % 4], amber, width=6)
    return bpy.app.icons.new_triangles((0, 255), bytes(coords), bytes(colors))


def _toolbar(self, context):
    if context.mode != "OBJECT":
        return
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
    # Use the same compact/wide layout as Blender's native tools, but invoke the
    # action directly: choosing an active WorkSpaceTool would require another click.
    generator, show_text = ToolSelectPanelHelper._layout_generator_detect_from_region(
        self.layout, context.region, 1.75,
    )
    generator.send(None)
    column = generator.send(False)
    column.operator_context = "EXEC_REGION_WIN"
    column.operator(
        "view3d.bonsai_section_box", text="Section Box" if show_text else "",
        icon_value=_toolbar_icon, icon="NONE" if _toolbar_icon else "CUBE",
        depress=_session is not None and _session["space"] == context.space_data,
    )
    if _session is not None:
        column = generator.send(False)
        column.operator_context = "EXEC_REGION_WIN"
        column.operator("view3d.bonsai_section_box_clear",
                        text="Clear Section Box" if show_text else "", icon="X")
    generator.send(None)
    self.layout.separator()


@persistent
def _load_pre(*args):
    clear_section()


@persistent
def _save_pre(*args):
    global _saving
    if _owner_valid():
        # Don't bake a temporary clipping region into the saved .blend.
        _saving = True
        old = _session["previous"]
        _session["rv3d"].clip_planes = old["clip_planes"]
        _session["rv3d"].use_clip_planes = old["use_clip_planes"]
        _session["rv3d"].use_box_clip = old["use_box_clip"]


@persistent
def _save_post(*args):
    global _saving
    _saving = False
    _apply()


_classes = (
    SECTIONBOX_PG_settings, SECTIONBOX_OT_create, SECTIONBOX_OT_clear,
    SECTIONBOX_OT_frame, SECTIONBOX_PT_panel,
)


def register():
    global _toolbar_icon
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bonsai_section_box = PointerProperty(type=SECTIONBOX_PG_settings)
    bpy.types.VIEW3D_MT_view.append(_view_menu)
    bpy.types.VIEW3D_HT_header.append(_header)
    if not bpy.app.background:
        _toolbar_icon = _create_toolbar_icon()
    bpy.types.VIEW3D_PT_tools_active.prepend(_toolbar)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        km = keyconfig.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("view3d.bonsai_section_box", "B", "PRESS", alt=True, shift=True)
        _keymaps.append((km, kmi))
    bpy.app.handlers.load_pre.append(_load_pre)
    bpy.app.handlers.save_pre.append(_save_pre)
    bpy.app.handlers.save_post.append(_save_post)
    if hasattr(bpy.app.handlers, "save_post_fail"):
        bpy.app.handlers.save_post_fail.append(_save_post)


def unregister():
    global _bonsai_hook, _saving, _toolbar_icon
    _saving = False
    clear_section()
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    if _bonsai_hook:
        cls, original, wrapper = _bonsai_hook
        if cls.refresh_clipping_planes is wrapper:
            cls.refresh_clipping_planes = original
        _bonsai_hook = None
    for handlers, function in (
        (bpy.app.handlers.load_pre, _load_pre),
        (bpy.app.handlers.save_pre, _save_pre),
        (bpy.app.handlers.save_post, _save_post),
        (getattr(bpy.app.handlers, "save_post_fail", []), _save_post),
    ):
        if function in handlers:
            handlers.remove(function)
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    bpy.types.VIEW3D_MT_view.remove(_view_menu)
    bpy.types.VIEW3D_HT_header.remove(_header)
    bpy.types.VIEW3D_PT_tools_active.remove(_toolbar)
    if _toolbar_icon:
        bpy.app.icons.release(_toolbar_icon)
        _toolbar_icon = 0
    del bpy.types.WindowManager.bonsai_section_box
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
