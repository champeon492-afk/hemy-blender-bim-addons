bl_info = {
    "name": "Bonsai Dynamic Dimension",
    "author": "OpenAI Codex",
    "version": (0, 3, 2),
    "blender": (5, 0, 0),
    "location": "3D View > Toolbar > Dynamic Dimension",
    "description": "Plan-only aligned dimensions between IFC references with editable wall placement",
    "category": "3D View",
}

import bpy
import blf
import gpu
from bpy.app.handlers import persistent
from bpy.props import (
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
    FloatVectorProperty,
)
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


AXES = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}
_draw_handle = None


def is_plan_view(context):
    """Only a top orthographic view is a plan view for this tool."""
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        return False
    rv3d = area.spaces.active.region_3d
    if rv3d is None or rv3d.view_perspective != "ORTHO":
        return False
    view_normal = rv3d.view_rotation @ Vector((0, 0, 1))
    return view_normal.z > 0.999


def horizontal(vector):
    value = Vector((vector.x, vector.y, 0.0))
    if value.length < 1e-7:
        raise ValueError("References must be separated in the plan plane")
    return value.normalized()


def is_wall(obj):
    element = ifc_entity(obj)
    return bool(element and element.is_a("IfcWall"))


def wall_face_reference(obj, world_point):
    """Snap a plan hit on a wall's top to its nearest vertical envelope face.

    The local envelope is stable under IFC placement edits. This is a geometric
    wall-face approximation; a regenerated wall profile can change the face.
    """
    local = obj.matrix_world.inverted_safe() @ world_point
    corners = [Vector(corner) for corner in obj.bound_box]
    xmin, xmax = min(p.x for p in corners), max(p.x for p in corners)
    ymin, ymax = min(p.y for p in corners), max(p.y for p in corners)
    candidates = (
        (abs(local.x - xmin), Vector((xmin, min(max(local.y, ymin), ymax), local.z)), Vector((-1, 0, 0))),
        (abs(local.x - xmax), Vector((xmax, min(max(local.y, ymin), ymax), local.z)), Vector((1, 0, 0))),
        (abs(local.y - ymin), Vector((min(max(local.x, xmin), xmax), ymin, local.z)), Vector((0, -1, 0))),
        (abs(local.y - ymax), Vector((min(max(local.x, xmin), xmax), ymax, local.z)), Vector((0, 1, 0))),
    )
    _, anchor, local_normal = min(candidates, key=lambda candidate: candidate[0])
    world_normal = horizontal(obj.matrix_world.to_3x3() @ local_normal)
    return anchor, local_normal, world_normal


def ifc_entity(obj):
    if obj is None:
        return None
    try:
        import bonsai.tool as tool
        return tool.Ifc.get_entity(obj)
    except (ImportError, AttributeError, RuntimeError):
        return None


def valid_element(obj):
    element = ifc_entity(obj)
    return bool(element and element.is_a("IfcProduct") and element.ObjectPlacement)


def world_bounds(obj):
    # Blender's local bound_box is inexpensive and follows the object's world matrix.
    # It is an envelope, not a topology-persistent IFC face reference.
    if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT"} or obj.data is None:
        raise ValueError("Envelope clearance requires geometry on both elements")
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def dimension_axis(item):
    if item.axis == "ALIGNED":
        axis = Vector(item.axis_vector)
        axis.z = 0.0
        if axis.length < 1e-9:
            raise ValueError("Aligned dimension has no valid direction")
        return axis.normalized()
    return AXES[item.axis]


def measure(item):
    a, b = item.reference, item.movable
    if not (valid_element(a) and valid_element(b)) or a == b:
        raise ValueError("Choose two different placed IFC products")
    axis = dimension_axis(item)
    if item.mode == "PICKED":
        pa = a.matrix_world @ Vector(item.anchor_a)
        pb = b.matrix_world @ Vector(item.anchor_b)
        signed = (pb - pa).dot(axis)
        return signed, pa, pb, 1.0
    if item.mode == "ORIGIN":
        pa = a.matrix_world.translation.copy()
        pb = b.matrix_world.translation.copy()
        sign = 1.0 if (pb - pa).dot(axis) >= 0 else -1.0
        value = sign * (pb - pa).dot(axis)
        return value, pa, pb, sign

    ac = world_bounds(a)
    bc = world_bounds(b)
    if not ac or not bc:
        raise ValueError("Both elements need visible bounds")
    av = [p.dot(axis) for p in ac]
    bv = [p.dot(axis) for p in bc]
    amin, amax = min(av), max(av)
    bmin, bmax = min(bv), max(bv)
    sign = 1.0 if (bmin + bmax) >= (amin + amax) else -1.0
    a_center = sum(ac, Vector()) / len(ac)
    b_center = sum(bc, Vector()) / len(bc)
    if sign > 0:
        value, a_plane, b_plane = bmin - amax, amax, bmin
    else:
        value, a_plane, b_plane = amin - bmax, amin, bmax
    # Put witness points at the centers of the projected bounding planes.
    pa = a_center + axis * (a_plane - a_center.dot(axis))
    pb = b_center + axis * (b_plane - b_center.dot(axis))
    return value, pa, pb, sign


class DD_Dimension(bpy.types.PropertyGroup):
    label: StringProperty(name="Label", default="Dimension")
    reference: PointerProperty(name="Fixed IFC element", type=bpy.types.Object)
    movable: PointerProperty(name="Movable IFC element", type=bpy.types.Object)
    axis: EnumProperty(name="Axis", items=[(x, x, "") for x in AXES] + [("ALIGNED", "Aligned", "Along the picked references")], default="X")
    mode: EnumProperty(
        name="Measure",
        items=[
            ("CLEARANCE", "Envelope clearance", "Gap between bounding extents along the axis"),
            ("ORIGIN", "Origin distance", "Distance between element origins along the axis"),
            ("PICKED", "Picked references", "Distance between picked points on the elements"),
        ],
        default="CLEARANCE",
    )
    target: FloatProperty(name="Target distance", subtype="DISTANCE", unit="LENGTH", min=0.0)
    anchor_a: FloatVectorProperty(size=3, subtype="XYZ")
    anchor_b: FloatVectorProperty(size=3, subtype="XYZ")
    axis_vector: FloatVectorProperty(size=3, subtype="XYZ", default=(1, 0, 0))
    offset_px: FloatVectorProperty(name="Witness offset", size=2, default=(0, 45))
    offset_m: FloatProperty(name="Dimension line offset", subtype="DISTANCE", unit="LENGTH", default=0.5)
    reference_kind: EnumProperty(
        name="Reference type",
        items=[("FACE", "Wall faces", "Parallel wall envelope faces"), ("POINT", "Points", "Picked IFC points")],
        default="POINT",
    )


class DD_OT_capture(bpy.types.Operator):
    bl_idname = "bonsai_dynamic_dimension.capture"
    bl_label = "Use Active IFC Element"
    bl_options = {"REGISTER", "UNDO"}

    slot: EnumProperty(items=[("REFERENCE", "Reference", ""), ("MOVABLE", "Movable", "")])

    def execute(self, context):
        obj = context.active_object
        if not valid_element(obj):
            self.report({"ERROR"}, "Select a placed IFC product first")
            return {"CANCELLED"}
        props = context.scene.bonsai_dynamic_dimension
        setattr(props, "draft_reference" if self.slot == "REFERENCE" else "draft_movable", obj)
        return {"FINISHED"}


class DD_OT_add(bpy.types.Operator):
    bl_idname = "bonsai_dynamic_dimension.add"
    bl_label = "Create Dynamic Dimension"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.bonsai_dynamic_dimension
        a, b = props.draft_reference, props.draft_movable
        if not valid_element(a) or not valid_element(b) or a == b:
            self.report({"ERROR"}, "Set two different IFC products as fixed and movable")
            return {"CANCELLED"}
        item = props.dimensions.add()
        item.reference, item.movable = a, b
        item.axis, item.mode = props.draft_axis, props.draft_mode
        item.label = f"{a.name} to {b.name}"
        props.active_index = len(props.dimensions) - 1
        try:
            item.target = max(0.0, measure(item)[0])
        except ValueError as exc:
            props.dimensions.remove(props.active_index)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class DD_OT_pick(bpy.types.Operator):
    bl_idname = "bonsai_dynamic_dimension.pick"
    bl_label = "Dynamic Dimension"
    bl_description = "In top plan, pick two parallel IFC wall faces or two IFC points, then place the aligned dimension"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return is_plan_view(context)

    def invoke(self, context, event):
        self.area = context.area
        self.region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
        self.rv3d = context.area.spaces.active.region_3d
        if self.region is None or self.rv3d is None:
            self.report({"ERROR"}, "Open a 3D viewport first")
            return {"CANCELLED"}
        self.stage = 0
        self.first_obj = None
        self.first_local = None
        self.first_normal = None
        self.second_obj = None
        self.second_local = None
        self.second_normal = None
        self.axis = None
        self.kind = "POINT"
        self.mouse_xy = Vector((0, 0))
        self.preview_handle = bpy.types.SpaceView3D.draw_handler_add(
            self.draw_preview, (), "WINDOW", "POST_PIXEL"
        )
        context.workspace.status_text_set("Aligned Dimension: pick the first IFC wall face or point (Esc cancels)")
        context.window_manager.modal_handler_add(self)
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def finish(self, context):
        context.workspace.status_text_set(None)
        bpy.types.SpaceView3D.draw_handler_remove(self.preview_handle, "WINDOW")
        self.area.tag_redraw()

    def point_at_mouse(self, context, event):
        xy = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        origin = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, xy)
        direction = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, xy)
        hit, location, normal, face_index, obj, matrix = context.scene.ray_cast(
            context.evaluated_depsgraph_get(), origin, direction
        )
        if not hit or not valid_element(obj):
            raise ValueError("Click visible geometry of a placed IFC element")
        if is_wall(obj):
            local, normal, world_normal = wall_face_reference(obj, location)
            return obj, local, normal, world_normal
        return obj, obj.matrix_world.inverted_safe() @ location, None, None

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.finish(context)
            return {"CANCELLED"}
        if not is_plan_view(context):
            self.report({"WARNING"}, "Aligned dimensions can only be placed in top orthographic plan")
            self.finish(context)
            return {"CANCELLED"}
        if context.area != self.area:
            return {"PASS_THROUGH"}
        self.mouse_xy = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        self.area.tag_redraw()
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"RUNNING_MODAL"}
        if self.stage < 2:
            try:
                obj, local_point, local_normal, world_normal = self.point_at_mouse(context, event)
            except ValueError as exc:
                self.report({"WARNING"}, str(exc))
                return {"RUNNING_MODAL"}
            if self.stage == 0:
                self.first_obj, self.first_local = obj, local_point
                self.first_normal = world_normal
                self.stage = 1
                context.workspace.status_text_set("Aligned Dimension: pick a parallel face on a second IFC wall, or another IFC point")
            else:
                if obj == self.first_obj:
                    self.report({"WARNING"}, "Pick a different IFC element")
                    return {"RUNNING_MODAL"}
                pa = self.first_obj.matrix_world @ self.first_local
                pb = obj.matrix_world @ local_point
                try:
                    if self.first_normal is not None and world_normal is not None:
                        if abs(self.first_normal.dot(world_normal)) < 0.98:
                            raise ValueError("Choose parallel wall faces")
                        axis = self.first_normal.copy()
                        if (pb - pa).dot(axis) < 0:
                            axis.negate()
                        kind = "FACE"
                    else:
                        axis = horizontal(pb - pa)
                        kind = "POINT"
                    if (pb - pa).dot(axis) < 1e-5:
                        raise ValueError("Choose references with a measurable gap")
                except ValueError as exc:
                    self.report({"WARNING"}, str(exc))
                    return {"RUNNING_MODAL"}
                self.second_obj, self.second_local = obj, local_point
                self.second_normal = world_normal
                self.axis, self.kind = axis, kind
                self.stage = 2
                context.workspace.status_text_set("Aligned Dimension: move away from the walls and click to place the line")
            return {"RUNNING_MODAL"}

        pa = self.first_obj.matrix_world @ self.first_local
        pb = self.second_obj.matrix_world @ self.second_local
        offset = plan_offset_at_mouse(self.region, self.rv3d, self.mouse_xy, pa, pb, self.axis)
        props = context.scene.bonsai_dynamic_dimension
        item = props.dimensions.add()
        item.reference = self.first_obj
        item.movable = self.second_obj
        item.mode = "PICKED"
        item.axis = "ALIGNED"
        item.anchor_a = self.first_local
        item.anchor_b = self.second_local
        item.axis_vector = self.axis
        item.reference_kind = self.kind
        item.offset_m = offset
        item.label = f"{self.first_obj.name} to {self.second_obj.name}"
        item.target = (pb - pa).dot(self.axis)
        props.active_index = len(props.dimensions) - 1
        self.finish(context)
        self.report({"INFO"}, "Aligned dimension placed. Select a referenced wall and use Edit Value in the Bonsai sidebar.")
        return {"FINISHED"}

    def draw_preview(self):
        if bpy.context.area != self.area or self.stage == 0:
            return
        pa = self.first_obj.matrix_world @ self.first_local
        p2a = view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, pa)
        if p2a is None:
            return
        if self.stage == 1:
            draw_lines_2d([(p2a, p2a + Vector((0, 12))),
                           (p2a + Vector((-6, 6)), p2a + Vector((6, 6)))], (1.0, 0.75, 0.2, 1.0))
            return
        pb = self.second_obj.matrix_world @ self.second_local
        p2b = view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, pb)
        if p2b is None:
            return
        offset = plan_offset_at_mouse(self.region, self.rv3d, self.mouse_xy, pa, pb, self.axis)
        mid = draw_aligned_dimension(self.region, self.rv3d, pa, pb, self.axis, offset, (1.0, 0.75, 0.2, 0.95))
        if mid is not None:
            draw_text_2d(mid, f"{(pb - pa).dot(self.axis):.3f} m", (1.0, 0.85, 0.35, 1.0))


class DD_OT_apply(bpy.types.Operator):
    bl_idname = "bonsai_dynamic_dimension.apply"
    bl_label = "Move IFC Element to Target"
    bl_options = {"REGISTER", "UNDO"}
    transaction_key = ""
    transaction_data = None
    move_side: EnumProperty(
        name="Move",
        items=[("MOVABLE", "Second element", "Move the second IFC element"),
               ("REFERENCE", "First element", "Move the first IFC element")],
        default="MOVABLE",
    )

    def _execute(self, context):
        import bonsai.tool as tool
        props = context.scene.bonsai_dynamic_dimension
        if props.active_index >= len(props.dimensions):
            self.report({"ERROR"}, "Choose a dimension")
            return {"CANCELLED"}
        item = props.dimensions[props.active_index]
        try:
            current, _, _, sign = measure(item)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        obj = item.reference if self.move_side == "REFERENCE" else item.movable
        element = tool.Ifc.get_entity(obj)
        if not element.ObjectPlacement.is_a("IfcLocalPlacement"):
            self.report({"ERROR"}, "Only IFC local placements are supported")
            return {"CANCELLED"}
        amount = (item.target - current) if item.mode == "PICKED" else sign * (item.target - current)
        if self.move_side == "REFERENCE":
            amount = -amount
        if abs(amount) < 1e-7:
            return {"FINISHED"}
        old_matrix = obj.matrix_world.copy()
        new_matrix = old_matrix.copy()
        new_matrix.translation += dimension_axis(item) * amount
        obj.matrix_world = new_matrix
        try:
            # Bonsai converts the Blender matrix to IFC coordinates and records
            # placement checksums. Its IFC operator base provides undo integration.
            tool.Geometry.run_edit_object_placement(obj)
        except Exception:
            obj.matrix_world = old_matrix
            raise
        self.report({"INFO"}, f"Moved {obj.name} by {abs(amount):.4f} m")
        return {"FINISHED"}

    def execute(self, context):
        try:
            import bonsai.tool as tool
        except ImportError:
            self.report({"ERROR"}, "Bonsai is not enabled")
            return {"CANCELLED"}
        # Delegate to Bonsai's IFC transaction and undo wrapper.
        return tool.Ifc.Operator.execute(self, context)

class DD_OT_remove(bpy.types.Operator):
    bl_idname = "bonsai_dynamic_dimension.remove"
    bl_label = "Remove Dynamic Dimension"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.bonsai_dynamic_dimension
        if props.active_index < len(props.dimensions):
            props.dimensions.remove(props.active_index)
            props.active_index = max(0, min(props.active_index, len(props.dimensions) - 1))
        return {"FINISHED"}


class DD_OT_edit_value(bpy.types.Operator):
    bl_idname = "bonsai_dynamic_dimension.edit_value"
    bl_label = "Set Dimension Value"
    bl_options = {"REGISTER", "UNDO"}

    target: FloatProperty(name="New distance", subtype="DISTANCE", unit="LENGTH", min=0.0)
    move_side: EnumProperty(
        name="Move",
        items=[
            ("MOVABLE", "Current movable element", "Move the element currently marked movable"),
            ("REFERENCE", "Current fixed reference", "Move the element currently marked fixed"),
        ],
        default="MOVABLE",
    )

    def invoke(self, context, event):
        props = context.scene.bonsai_dynamic_dimension
        if props.active_index >= len(props.dimensions):
            self.report({"ERROR"}, "Choose a dimension first")
            return {"CANCELLED"}
        item = props.dimensions[props.active_index]
        try:
            self.target = measure(item)[0]
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.move_side = "REFERENCE" if context.active_object == item.reference else "MOVABLE"
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        props = context.scene.bonsai_dynamic_dimension
        if props.active_index >= len(props.dimensions):
            return {"CANCELLED"}
        item = props.dimensions[props.active_index]
        item.target = self.target
        return bpy.ops.bonsai_dynamic_dimension.apply(move_side=self.move_side)


class DD_Properties(bpy.types.PropertyGroup):
    draft_reference: PointerProperty(name="Fixed", type=bpy.types.Object)
    draft_movable: PointerProperty(name="Movable", type=bpy.types.Object)
    draft_axis: EnumProperty(name="Axis", items=[(x, x, "") for x in AXES], default="X")
    draft_mode: EnumProperty(
        name="Measure",
        items=[("CLEARANCE", "Envelope clearance", ""), ("ORIGIN", "Origin distance", "")],
        default="CLEARANCE",
    )
    dimensions: CollectionProperty(type=DD_Dimension)
    active_index: IntProperty(default=0, min=0)


class DD_UL_dimensions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        try:
            value = f"  {measure(item)[0]:.3f} m"
        except ValueError:
            value = "  (unresolved)"
        layout.label(text=item.label[:42] + value, icon="DRIVER_DISTANCE")


class DD_PT_panel(bpy.types.Panel):
    bl_label = "Aligned Dimension"
    bl_idname = "DD_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai"

    @classmethod
    def poll(cls, context):
        return is_plan_view(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.bonsai_dynamic_dimension
        box = layout.box()
        box.label(text="Plan view: pick two IFC references")
        box.operator("bonsai_dynamic_dimension.pick", text="Aligned Dimension", icon="DRIVER_DISTANCE")
        box.label(text="Click two walls, then place the line")
        if len(props.dimensions):
            row = box.row(align=True)
            row.operator("bonsai_dynamic_dimension.edit_value", text="Edit Value / Move", icon="DRIVER_DISTANCE")
            row.operator("bonsai_dynamic_dimension.remove", text="", icon="X")
        layout.template_list("DD_UL_dimensions", "", props, "dimensions", props, "active_index", rows=3)
        if props.active_index < len(props.dimensions):
            item = props.dimensions[props.active_index]
            box = layout.box()
            box.prop(item, "label")
            box.label(text=f"Reference: {item.reference.name if item.reference else '(missing)'}")
            box.label(text=f"Movable: {item.movable.name if item.movable else '(missing)'}")
            try:
                current = measure(item)[0]
                box.label(text=f"Current: {current:.4f} m")
            except ValueError as exc:
                box.label(text=str(exc), icon="ERROR")
        layout.label(text="Select a referenced wall before Edit Value.", icon="INFO")


def draw_lines_2d(segments, color):
    coords = []
    for start, end in segments:
        coords.extend((tuple(start), tuple(end)))
    if not coords:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(2.0)
    batch = batch_for_shader(shader, "LINES", {"pos": coords})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set("NONE")


def draw_witnesses(p2a, p2b, offset, color):
    a1, b1 = p2a + offset, p2b + offset
    draw_lines_2d([(p2a, a1), (p2b, b1), (a1, b1)], color)


def plan_offset_at_mouse(region, rv3d, mouse_xy, pa, pb, axis):
    location = view3d_utils.region_2d_to_location_3d(region, rv3d, mouse_xy, pa)
    tangent = Vector((-axis.y, axis.x, 0))
    midpoint = (pa + pb) * 0.5
    return (location - midpoint).dot(tangent)


def draw_aligned_dimension(region, rv3d, pa, pb, axis, offset_m, color):
    tangent = Vector((-axis.y, axis.x, 0))
    distance = (pb - pa).dot(axis)
    a_line = pa + tangent * offset_m
    b_line = pa + axis * distance + tangent * offset_m
    projected = [view3d_utils.location_3d_to_region_2d(region, rv3d, p) for p in (pa, pb, a_line, b_line)]
    if any(p is None for p in projected):
        return None
    p2a, p2b, a2, b2 = projected
    draw_lines_2d([(p2a, a2), (p2b, b2), (a2, b2)], color)
    return (a2 + b2) * 0.5


def draw_text_2d(location, value, color):
    blf.position(0, location.x + 8, location.y + 8, 0)
    blf.size(0, 14)
    blf.color(0, *color)
    blf.draw(0, value)


def draw_dimensions():
    context = bpy.context
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None or context.scene is None or not is_plan_view(context):
        return
    props = context.scene.bonsai_dynamic_dimension
    for item in props.dimensions:
        try:
            value, pa, pb, _ = measure(item)
        except (ValueError, ReferenceError):
            continue
        mid = draw_aligned_dimension(region, rv3d, pa, pb, dimension_axis(item), item.offset_m, (0.15, 0.9, 0.7, 0.95))
        if mid is not None:
            draw_text_2d(mid, f"{value:.3f} m", (0.1, 1.0, 0.75, 1.0))


@persistent
def redraw_on_update(scene, depsgraph):
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def draw_toolbar(self, context):
    if context.mode != "OBJECT" or not is_plan_view(context):
        return
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper

    generator, show_text = ToolSelectPanelHelper._layout_generator_detect_from_region(
        self.layout, context.region, 1.75
    )
    generator.send(None)
    column = generator.send(False)
    column.operator_context = "INVOKE_REGION_WIN"
    column.operator(
        "bonsai_dynamic_dimension.pick",
        text="Aligned Dimension" if show_text else "",
        icon="DRIVER_DISTANCE",
    )
    generator.send(None)
    self.layout.separator()


CLASSES = (
    DD_Dimension, DD_Properties, DD_OT_capture, DD_OT_add, DD_OT_pick,
    DD_OT_apply, DD_OT_remove, DD_OT_edit_value, DD_UL_dimensions, DD_PT_panel,
)


def register():
    global _draw_handle
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bonsai_dynamic_dimension = PointerProperty(type=DD_Properties)
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_dimensions, (), "WINDOW", "POST_PIXEL")
    bpy.app.handlers.depsgraph_update_post.append(redraw_on_update)
    bpy.types.VIEW3D_PT_tools_active.prepend(draw_toolbar)


def unregister():
    global _draw_handle
    bpy.types.VIEW3D_PT_tools_active.remove(draw_toolbar)
    if redraw_on_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(redraw_on_update)
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    del bpy.types.Scene.bonsai_dynamic_dimension
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
