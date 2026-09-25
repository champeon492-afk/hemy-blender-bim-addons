"""Storey-scoped IFC driving dimensions for Bonsai. GPL-3.0-or-later."""
bl_info = {
    "name": "Bonsai Driving Plan Dimensions", "author": "Bonsai Plan Dimensions contributors",
    "version": (0, 1, 1), "blender": (4, 5, 0),
    "location": "3D View > Sidebar > Plan Dimensions", "category": "3D View",
    "description": "Storey-specific editable dimensions for IFC walls and grids",
}

import copy
import traceback
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.app.handlers import persistent
from . import adapter, overlay

_storey_items = []
_keymaps = []


def storeys(self, context):
    global _storey_items
    try:
        _, f, _, _, _ = adapter.api()
        items = [(s.GlobalId, s.Name or f"Storey #{s.id()}", "IFC building storey")
                 for s in sorted(f.by_type("IfcBuildingStorey"), key=lambda s: (s.Elevation or 0, s.Name or ""))]
    except ValueError:
        items = []
    # Retain strings for Blender's dynamic EnumProperty lifetime requirement.
    if items != _storey_items:
        _storey_items = items
    return _storey_items


def redraw(self, context):
    adapter.invalidate()


class BPDProperties(bpy.types.PropertyGroup):
    storey_guid: EnumProperty(name="Building storey", items=storeys, update=redraw)
    show_dimensions: BoolProperty(name="Show dimensions", default=True, update=redraw)
    active_dimension: StringProperty()
    wall_mode: EnumProperty(name="Wall reference", items=[
        ("CLEAR", "Clear faces", "Distance between the facing wall surfaces"),
        ("AXIS", "Body centres", "Distance between wall body centre lines")], default="CLEAR")
    offset: FloatProperty(name="Line offset", subtype="DISTANCE", unit="LENGTH", default=1.0)


class IFCOperation:
    """Use Bonsai's transaction wrapper while allowing enable-before-Bonsai."""
    def _execute(self, context):
        _, f, _, _, _ = adapter.api()
        transaction = f.transaction
        try:
            return self._execute_impl(context)
        except Exception:
            # IfcOpenShell temporarily suspends its journal in create_entity.
            # Some attribute errors escape without restoring it. Preserve the
            # active journal so Bonsai closes and rolls back this operation.
            if f.transaction is None:
                f.transaction = transaction
            raise

    def execute(self, context):
        try:
            self.prepare(context)
        except (ValueError, KeyError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        # Validation happens before entering a transaction. IFC and Blender undo
        # are coordinated by the exact wrapper used by tool.Ifc.Operator.
        tool, f, _, _, store = adapter.api()
        if store.current_transaction:
            self.report({"ERROR"}, "Finish the active Bonsai operation first")
            return {"CANCELLED"}
        previous_key = store.history[-1]["key"] if store.history else None
        previous_future = list(store.future)
        previous_ifc_future = list(f.future)
        previous_last = store.last_transaction
        bim_props = tool.Blender.get_bim_props()
        previous_prop_key = bim_props.last_transaction
        previous_dirty = bim_props.is_dirty
        previous_active = context.scene.BPDProperties.active_dimension
        snapshots = []
        if hasattr(self, "spec"):
            for _, obj in adapter.affected_objects(self.spec, self.moving):
                object_props = tool.Blender.get_object_bim_props(obj)
                metadata = {key: getattr(object_props, key) for key in
                            ("location_checksum", "rotation_checksum", "blender_offset_type")}
                snapshots.append((obj, obj.matrix_world.copy(), metadata))
        try:
            store.execute_ifc_operator(self, context)
        except Exception as exc:
            # Bonsai closes the failed IFC transaction but deliberately does
            # not revert it. Revert just this operation, retaining prior redo.
            traceback.print_exc()
            store.undo(until_key=previous_key)
            store.future = previous_future
            f.future = previous_ifc_future
            store.last_transaction = previous_last
            bim_props.last_transaction = previous_prop_key
            bim_props.is_dirty = previous_dirty
            context.scene.BPDProperties.active_dimension = previous_active
            for obj, matrix, metadata in snapshots:
                obj.matrix_world = matrix
                object_props = tool.Blender.get_object_bim_props(obj)
                for key, value in metadata.items():
                    setattr(object_props, key, value)
                entity = tool.Ifc.get_entity(obj)
                if entity:
                    tool.Geometry.clear_cache(entity)
            context.view_layer.update()
            adapter.invalidate()
            self.report({"ERROR"}, f"Dimension edit rolled back: {exc}")
            return {"CANCELLED"}
        adapter.invalidate()
        return {"FINISHED"}


class BPD_OT_create(IFCOperation, bpy.types.Operator):
    bl_idname = "bpd.create"
    bl_label = "Add Driving Dimension"
    bl_description = "Keep the first element fixed; the active (last selected) element is end B"
    bl_options = {"REGISTER", "UNDO"}
    make_chain: BoolProperty(default=False)

    def prepare(self, context):
        self.specs = adapter.selected_specs(context, self.make_chain)

    def _execute_impl(self, context):
        for spec in self.specs:
            context.scene.BPDProperties.active_dimension = adapter.save_spec(spec)
        self.report({"INFO"}, f"Created {len(self.specs)} driving dimension(s)")


class BPD_OT_edit(IFCOperation, bpy.types.Operator):
    bl_idname = "bpd.edit"
    bl_label = "Edit Driving Dimension"
    bl_description = "Set a distance and move the selected end in the IFC model"
    bl_options = {"REGISTER", "UNDO"}
    guid: StringProperty()
    target: FloatProperty(name="Distance", subtype="DISTANCE", unit="LENGTH", min=0.000001, precision=4)
    moving: EnumProperty(name="Move", items=[("B", "End B", "Keep A fixed"), ("A", "End A", "Keep B fixed")])
    offset: FloatProperty(name="Line offset", subtype="DISTANCE", unit="LENGTH", default=1.0)

    def invoke(self, context, event):
        try:
            guid, name, spec = adapter.get_dimension(self.guid or context.scene.BPDProperties.active_dimension)
            self.guid = guid
            self.target = adapter.measurement(spec).value / (context.scene.unit_settings.scale_length or 1)
            self.moving = spec["moving"]
            self.offset = spec["offset"] / (context.scene.unit_settings.scale_length or 1)
            context.scene.BPDProperties.active_dimension = guid
        except (ValueError, KeyError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        try:
            _, name, spec = adapter.get_dimension(self.guid)
            layout.label(text=name, icon="DRIVER_DISTANCE")
            for key in ("a", "b"):
                entity, obj = adapter.resolve(spec[key])
                layout.label(text=f"{key.upper()}: {getattr(entity, 'AxisTag', None) or entity.Name or obj.name}")
            layout.label(text="Clear faces" if spec["mode"] == "CLEAR" else "Centre / axis spacing")
        except ValueError:
            pass
        layout.prop(self, "target")
        layout.prop(self, "moving", expand=True)
        layout.prop(self, "offset")

    def prepare(self, context):
        if context.mode != "OBJECT":
            raise ValueError("Finish editing geometry first")
        _, _, spec = adapter.get_dimension(self.guid or context.scene.BPDProperties.active_dimension)
        self.spec = copy.deepcopy(spec)
        scale = context.scene.unit_settings.scale_length or 1
        self.metres = self.target * scale
        self.spec["moving"] = self.moving
        self.spec["offset"] = self.offset * scale
        adapter.prepare_move(self.spec, self.metres, self.moving)

    def _execute_impl(self, context):
        adapter.move(self.spec, self.metres, self.moving)
        achieved = adapter.measurement(self.spec).value
        if abs(achieved - self.metres) > max(1e-5, self.metres * 1e-6):
            raise ValueError("The model could not achieve the requested distance")
        adapter.save_spec(self.spec, self.guid or context.scene.BPDProperties.active_dimension)
        self.report({"INFO"}, "IFC element moved; save your IFC project to keep the change")


class BPD_OT_remove(IFCOperation, bpy.types.Operator):
    bl_idname = "bpd.remove"
    bl_label = "Remove Dimension"
    bl_description = "Remove the dimension without moving either element"
    bl_options = {"REGISTER", "UNDO"}
    guid: StringProperty()

    def prepare(self, context):
        adapter.get_dimension(self.guid)

    def _execute_impl(self, context):
        adapter.delete(self.guid)
        if context.scene.BPDProperties.active_dimension == self.guid:
            context.scene.BPDProperties.active_dimension = ""


class BPD_OT_refresh(bpy.types.Operator):
    bl_idname = "bpd.refresh"
    bl_label = "Refresh Dimensions"
    def execute(self, context):
        adapter.invalidate()
        return {"FINISHED"}


class BPD_OT_top(bpy.types.Operator):
    bl_idname = "bpd.top"
    bl_label = "Top Plan View"
    def execute(self, context):
        if context.area.type == "VIEW_3D":
            region = next(r for r in context.area.regions if r.type == "WINDOW")
            with context.temp_override(region=region):
                bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        return {"FINISHED"}


class BPD_OT_pick(bpy.types.Operator):
    bl_idname = "bpd.pick"
    bl_label = "Edit Dimension Label"
    bl_description = "Click a dimension value to edit it; Esc to cancel"

    def invoke(self, context, event):
        if not overlay.is_plan(context):
            if context.region.type == "WINDOW":
                return {"PASS_THROUGH"}
            self.report({"WARNING"}, "Switch to a plan view first")
            return {"CANCELLED"}
        if context.region.type == "WINDOW":
            hit = overlay.hit_test(context, event.mouse_region_x, event.mouse_region_y)
            if hit:
                bpy.ops.bpd.edit("INVOKE_DEFAULT", guid=hit)
                return {"FINISHED"}
            # Shortcut does not intercept double-click on ordinary geometry.
            return {"PASS_THROUGH"}
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Click a dimension value to edit. Esc to cancel.")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            context.area.header_text_set(None)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
            if region:
                x, y = event.mouse_x - region.x, event.mouse_y - region.y
                with context.temp_override(region=region):
                    hit = overlay.hit_test(context, x, y)
                    if hit:
                        context.area.header_text_set(None)
                        bpy.ops.bpd.edit("INVOKE_DEFAULT", guid=hit)
                        return {"FINISHED"}
        return {"PASS_THROUGH"}


class BPD_PT_main(bpy.types.Panel):
    bl_label = "Driving Plan Dimensions"
    bl_idname = "BPD_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Plan Dimensions"

    def draw(self, context):
        layout = self.layout
        try:
            adapter.api()
        except ValueError as exc:
            layout.label(text=str(exc), icon="INFO")
            return
        props = context.scene.BPDProperties
        layout.prop(props, "storey_guid")
        layout.prop(props, "show_dimensions")
        if not overlay.is_plan(context):
            layout.label(text="Dimensions appear only in plan", icon="INFO")
            layout.operator("bpd.top", icon="AXIS_TOP")
        box = layout.box()
        box.label(text="Create", icon="ADD")
        box.prop(props, "wall_mode")
        box.prop(props, "offset")
        row = box.row()
        row.enabled = overlay.is_plan(context) and context.mode == "OBJECT"
        row.operator("bpd.create", text="Dimension 2 Selected")
        row = box.row()
        row.enabled = overlay.is_plan(context) and context.mode == "OBJECT"
        row.operator("bpd.create", text="Chain Selected Grids").make_chain = True
        box.label(text="Last selected = moving end B", icon="INFO")
        row = layout.row(align=True)
        row.operator("bpd.pick", text="Pick Value", icon="EYEDROPPER")
        row.operator("bpd.refresh", text="", icon="FILE_REFRESH")
        try:
            entries = [e for e in adapter.dimensions() if e[2]["storey"] == props.storey_guid]
        except ValueError:
            entries = []
        if not entries:
            layout.label(text="No dimensions on this storey")
        for guid, name, spec in entries:
            box = layout.box()
            box.label(text=name)
            try:
                value = adapter.format_length(adapter.measurement(spec).value, context)
                row = box.row(align=True)
                row.operator("bpd.edit", text=value, icon="DRIVER_DISTANCE").guid = guid
                row.operator("bpd.remove", text="", icon="X").guid = guid
            except (ValueError, KeyError, ReferenceError) as exc:
                box.label(text=str(exc), icon="ERROR")
                box.operator("bpd.remove", text="Remove stale dimension").guid = guid
        layout.label(text="Double-click a plan value to edit")


@persistent
def refresh_handler(*_):
    adapter.invalidate()


classes = (BPDProperties, BPD_OT_create, BPD_OT_edit, BPD_OT_remove,
           BPD_OT_refresh, BPD_OT_top, BPD_OT_pick, BPD_PT_main)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.BPDProperties = PointerProperty(type=BPDProperties)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if refresh_handler not in handlers:
            handlers.append(refresh_handler)
    overlay.register()
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("bpd.pick", "LEFTMOUSE", "DOUBLE_CLICK")
        _keymaps.append((km, kmi))


def unregister():
    overlay.unregister()
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if refresh_handler in handlers:
            handlers.remove(refresh_handler)
    del bpy.types.Scene.BPDProperties
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
