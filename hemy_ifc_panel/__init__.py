"""Hemy 360 IFC Element Properties: a native Blender/Bonsai panel prototype."""

bl_info = {
    "name": "Hemy 360 — IFC Element Properties",
    "author": "Hemy Solutions",
    "version": (0, 4, 2),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Hemy IFC",
    "description": "Inspect IFC properties and toggle viewport visibility by IFC class",
    "category": "3D View",
}

import importlib
import json
from pathlib import Path
import textwrap

import bpy
from bpy.props import PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper

from .ifc_data import display_value, read_element
from . import wall_authoring

def bonsai_tool():
    """The extension entry point and Bonsai's Python tools are separate packages.

    Bonsai 0.8.5 registers as bl_ext.<repository>.bonsai, but ships its tools
    in a dependency wheel imported as bonsai.tool. Importing <addon>.tool
    fails for that layout even while Bonsai is enabled and a model is open.
    """
    enabled = [addon.module for addon in bpy.context.preferences.addons
               if addon.module == "bonsai" or addon.module.endswith(".bonsai")]
    if not enabled:
        return None
    candidates = list(dict.fromkeys(["bonsai.tool"] + [name + ".tool" for name in enabled]))
    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            # Skip only a missing candidate/package, not a missing dependency
            # inside an otherwise installed tool module.
            if error.name == module_name or module_name.startswith(error.name + "."):
                continue
            raise
        if hasattr(module, "Ifc"):
            return module
    return None


def snapshot(context):
    tool = bonsai_tool()
    if tool is None:
        return None, "Enable Bonsai to inspect IFC elements."
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        return None, "Open an IFC model in Bonsai."
    active_object = context.active_object
    if active_object is None or not active_object.select_get():
        return None, "Select an IFC element in the viewport."
    entity = tool.Ifc.get_entity(active_object)
    if entity is None:
        return None, "The selected object has no IFC element."
    return read_element(entity, ifc_file), f"{ifc_file.schema} / Selected element"


def class_objects(context, tool):
    """Group Bonsai-linked objects in the active view layer by IFC class."""
    groups = {}
    for obj in context.view_layer.objects:
        entity = tool.Ifc.get_entity(obj)
        if entity is not None:
            groups.setdefault(entity.is_a(), []).append(obj)
    return groups


def groups_for(data):
    return [
        ("attributes", "Attributes", "PROPERTIES", [{"name": "", "rows": data["attributes"]}]),
        ("psets", "Property Sets", "PRESET", data["psets"]),
        ("quantities", "Quantities", "DRIVER_DISTANCE", data.get("qsets") or [{"name": "", "rows": data["quantities"]}]),
        ("materials", "Materials", "MATERIAL", [{"name": "", "rows": data["materials"]}]),
        ("classification", "Classification", "BOOKMARKS", [{"name": "", "rows": data["classification"]}]),
        ("spatial", "Spatial Location", "OUTLINER_COLLECTION", [{"name": "", "rows": data["spatial"]}]),
        ("type", "Assigned Type", "OBJECT_DATA", [{"name": "", "rows": [["Type Name", data["type"]]]}]),
    ]


def wrap_label(layout, value, width, icon="NONE"):
    for index, line in enumerate(textwrap.wrap(display_value(value), width=max(10, width)) or [""]):
        layout.label(text=line, icon=icon if index == 0 else "NONE")


def draw_rows(layout, rows, region_width):
    box = layout.box()
    # Blender scales its controls with the user's UI scale. Keep long IDs readable.
    scale = bpy.context.preferences.system.ui_scale
    width = max(150, region_width / scale - 60)
    for key, value in rows:
        split = box.split(factor=0.43)
        left = split.column()
        left.enabled = False
        wrap_label(left, key, int(width * 0.43 / 7))
        right = split.column()
        if isinstance(value, bool):
            right.label(text=display_value(value), icon="CHECKBOX_HLT" if value else "CHECKBOX_DEHLT")
        else:
            wrap_label(right, value, int(width * 0.57 / 7))


class HEMY_PG_settings(PropertyGroup):
    search: StringProperty(name="Search Properties", description="Filter by section, set, property name or value", options={"TEXTEDIT_UPDATE"})
    class_search: StringProperty(name="Search IFC Classes", description="Filter IFC classes shown in the visibility panel", options={"TEXTEDIT_UPDATE"})


class HEMY_OT_toggle_ifc_class_visibility(Operator):
    bl_idname = "hemy.toggle_ifc_class_visibility"
    bl_label = "Toggle IFC Class Visibility"
    bl_description = "Hide or show every object of this IFC class in the active view layer"
    bl_options = {"REGISTER", "UNDO"}
    ifc_class: StringProperty(name="IFC Class")

    def execute(self, context):
        tool = bonsai_tool()
        if tool is None or tool.Ifc.get() is None:
            self.report({"WARNING"}, "Enable Bonsai and open an IFC model")
            return {"CANCELLED"}
        objects = class_objects(context, tool).get(self.ifc_class, [])
        if not objects:
            self.report({"WARNING"}, f"No {self.ifc_class} objects in the active view layer")
            return {"CANCELLED"}
        hide = not all(obj.hide_get(view_layer=context.view_layer) for obj in objects)
        for obj in objects:
            obj.hide_set(hide, view_layer=context.view_layer)
        self.report({"INFO"}, f"{'Hid' if hide else 'Showed'} {len(objects)} {self.ifc_class} objects")
        return {"FINISHED"}


class HEMY_OT_copy(Operator):
    bl_idname = "hemy.copy_global_id"
    bl_label = "Copy GlobalId"
    bl_description = "Copy this IFC element's GlobalId"
    value: StringProperty()

    def execute(self, context):
        context.window_manager.clipboard = self.value
        self.report({"INFO"}, "GlobalId copied")
        return {"FINISHED"}


class HEMY_OT_export(Operator, ExportHelper):
    bl_idname = "hemy.export_element_json"
    bl_label = "Export Element JSON"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    _payload = None

    def invoke(self, context, event):
        try:
            data, source = snapshot(context)
            if data is None:
                self.report({"WARNING"}, source)
                return {"CANCELLED"}
            self._payload = {"source": source, "element": data}
            safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in data["name"])
            self.filepath = safe_name + ".json"
            return ExportHelper.invoke(self, context, event)
        except Exception as error:
            self.report({"ERROR"}, f"Unable to read IFC element: {error}")
            return {"CANCELLED"}

    def execute(self, context):
        try:
            payload = self._payload
            if payload is None:
                data, source = snapshot(context)
                if data is None:
                    self.report({"WARNING"}, source)
                    return {"CANCELLED"}
                payload = {"source": source, "element": data}
            Path(self.filepath).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as error:
            self.report({"ERROR"}, f"Unable to export element: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Element JSON exported")
        return {"FINISHED"}


class HEMY_PT_class_visibility(Panel):
    bl_idname = "HEMY_PT_class_visibility"
    bl_label = "IFC Class Visibility"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hemy IFC"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        tool = bonsai_tool()
        if tool is None:
            layout.label(text="Enable Bonsai to control IFC visibility", icon="INFO")
            return
        if tool.Ifc.get() is None:
            layout.label(text="Open an IFC model in Bonsai", icon="INFO")
            return
        groups = class_objects(context, tool)
        if not groups:
            layout.label(text="No IFC objects in this view layer", icon="INFO")
            return
        props = context.scene.hemy_ifc_panel
        layout.prop(props, "class_search", text="", icon="VIEWZOOM")
        query = props.class_search.strip().casefold()
        shown = 0
        for ifc_class, objects in sorted(groups.items()):
            if query and query not in ifc_class.casefold():
                continue
            shown += 1
            all_hidden = all(obj.hide_get(view_layer=context.view_layer) for obj in objects)
            row = layout.row(align=True)
            row.label(text=f"{ifc_class} ({len(objects)})")
            op = row.operator(
                "hemy.toggle_ifc_class_visibility",
                text="Show" if all_hidden else "Hide",
                icon="HIDE_ON" if all_hidden else "HIDE_OFF",
            )
            op.ifc_class = ifc_class
        if not shown:
            layout.label(text="No IFC classes found", icon="VIEWZOOM")


class HEMY_PT_element_properties(Panel):
    bl_idname = "HEMY_PT_element_properties"
    bl_label = "Hemy 360 | Element Properties"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hemy IFC"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        props = context.scene.hemy_ifc_panel
        layout.use_property_split = False
        try:
            data, source = snapshot(context)
        except Exception as error:
            box = layout.box()
            box.label(text="Unable to read IFC element", icon="ERROR")
            wrap_label(box, str(error), 40)
            return
        if data is None:
            wrap_label(layout, source, 38, icon="INFO")
            return
        identity = layout.box()
        identity.label(text=data["ifcClass"], icon="OUTLINER_OB_MESH")
        wrap_label(identity, data["name"], 38)
        if data.get("description"):
            wrap_label(identity, data["description"], 38)
        row = identity.row(align=True)
        wrap_label(row.column(), data["globalId"], 32)
        op = row.operator("hemy.copy_global_id", text="", icon="COPYDOWN")
        op.value = data["globalId"] or ""
        identity.label(text=source, icon="INFO")
        layout.prop(props, "search", text="", icon="VIEWZOOM")
        query = props.search.strip().casefold()
        shown = 0
        for key, label, icon_name, sets in groups_for(data):
            filtered = []
            for property_set in sets:
                rows = [row for row in property_set["rows"] if not query or query in " ".join([
                    label, property_set["name"], str(row[0]), display_value(row[1])
                ]).casefold()]
                if rows:
                    filtered.append({**property_set, "rows": rows})
            if query and not filtered:
                continue
            count = sum(len(s["rows"]) for s in filtered)
            shown += count
            if query:
                layout.label(text=f"{label} ({count})", icon=icon_name)
                body = layout.column()
            else:
                header, body = layout.panel("hemy_" + key, default_closed=key not in {"attributes", "psets", "quantities"})
                header.label(text=f"{label} ({count})", icon=icon_name)
            if body is not None:
                if not filtered:
                    body.label(text="Not assigned")
                for property_set in filtered:
                    if property_set["name"]:
                        wrap_label(body, property_set["name"], 40)
                        body.label(text=property_set.get("source", "Occurrence"), icon="INFO")
                    draw_rows(body, property_set["rows"], context.region.width)
        if query and not shown:
            layout.label(text="No properties found", icon="VIEWZOOM")
            layout.label(text="Try a different name or value.")
        layout.separator()
        layout.operator("hemy.export_element_json", text="Export JSON", icon="EXPORT")


CLASSES = (
    HEMY_PG_settings,
    HEMY_OT_copy,
    HEMY_OT_export,
    HEMY_OT_toggle_ifc_class_visibility,
    HEMY_PT_class_visibility,
    HEMY_PT_element_properties,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hemy_ifc_panel = PointerProperty(type=HEMY_PG_settings)
    wall_authoring.register()


def unregister():
    wall_authoring.unregister()
    if hasattr(bpy.types.Scene, "hemy_ifc_panel"):
        del bpy.types.Scene.hemy_ifc_panel
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
