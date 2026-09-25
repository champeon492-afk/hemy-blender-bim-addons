"""Photo-assisted wall type authoring for an open Bonsai IFC project."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.request import Request, urlopen

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper, ImportHelper


class HEMY_PG_wall_layer(PropertyGroup):
    layer_name: StringProperty(name="Layer role", default="Layer")
    material_name: StringProperty(name="Material name", default="")
    thickness: FloatProperty(name="Thickness", default=0.01, min=0.0, unit="LENGTH", precision=3)
    is_ventilated: BoolProperty(name="Ventilated cavity", default=False)
    color: FloatVectorProperty(name="Viewport color", subtype="COLOR", size=4,
                               min=0.0, max=1.0, default=(0.75, 0.75, 0.75, 1.0))


class HEMY_PG_wall_authoring(PropertyGroup):
    image_path: StringProperty(name="Image", subtype="FILE_PATH")
    type_name: StringProperty(name="Wall type", default="Photo wall type")
    # Retained for opening Blender files authored with add-on versions 0.3.0-0.3.2.
    surface_name: StringProperty(name="Legacy visible finish", default="Wall finish")
    core_name: StringProperty(name="Legacy core material", default="")
    finish_thickness: FloatProperty(name="Legacy finish thickness", default=0.01, min=0.0, unit="LENGTH")
    core_thickness: FloatProperty(name="Legacy core thickness", default=0.20, min=0.0, unit="LENGTH")
    layers: CollectionProperty(type=HEMY_PG_wall_layer)
    active_layer: IntProperty(name="Active layer", default=0, min=0)
    preview_height: FloatProperty(name="Preview height", default=2.8, min=0.1, unit="LENGTH")
    preview_length: FloatProperty(name="Preview length", default=2.0, min=0.1, unit="LENGTH")
    confirmed: BoolProperty(name="I have checked the wall build-up", default=False)
    ai_note: StringProperty(name="AI suggestion")


@dataclass(frozen=True)
class LayerSpec:
    name: str
    material_name: str
    thickness: float
    is_ventilated: bool
    color: tuple[float, float, float, float]


def collect_layers(props):
    if not props.layers:
        raise ValueError("Add at least one wall layer")
    specs = []
    for position, layer in enumerate(props.layers, start=1):
        name = layer.layer_name.strip()
        material_name = layer.material_name.strip()
        if not name or not material_name:
            raise ValueError(f"Layer {position} needs a role and material name")
        if layer.thickness <= 0:
            raise ValueError(f"Layer {position} needs positive thickness")
        specs.append(LayerSpec(name, material_name, layer.thickness,
                               layer.is_ventilated, tuple(layer.color)))
    return specs


def image_file(props):
    path = Path(bpy.path.abspath(props.image_path)).expanduser()
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("Choose an existing PNG, JPEG, or WebP image")
    return path


def make_material(spec, path=None, type_guid=None, layer_index=None):
    material = bpy.data.materials.new(name=spec.material_name)
    material.use_nodes = True
    material.diffuse_color = spec.color
    if type_guid is not None:
        material["hemy_ifc_type_guid"] = type_guid
        material["hemy_layer_index"] = layer_index
    nodes = material.node_tree.nodes
    nodes.clear()
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    shader.inputs["Base Color"].default_value = spec.color
    if path is not None:
        image = nodes.new("ShaderNodeTexImage")
        image.image = bpy.data.images.load(str(path), check_existing=True)
        image.image.pack()
        nodes.active = image
        image.select = True
        material.node_tree.links.new(image.outputs["Color"], shader.inputs["Base Color"])
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def make_preview(props, ifc_type, specs, materials):
    length, height = props.preview_length, props.preview_height
    boundaries = [0.0]
    for spec in specs:
        boundaries.append(boundaries[-1] + spec.thickness)
    vertices = []
    for y in boundaries:
        vertices.extend([(0, y, 0), (length, y, 0), (length, y, height), (0, y, height)])
    faces, slots = [], []
    for index in range(len(specs)):
        a, b = index * 4, (index + 1) * 4
        # Exposed ends, top and bottom receive each layer's material slot.
        faces.extend([(a, a + 3, b + 3, b), (a + 1, b + 1, b + 2, a + 2),
                      (a + 3, a + 2, b + 2, b + 3), (a, b, b + 1, a + 1)])
        slots.extend([index] * 4)
    faces.extend([(0, 1, 2, 3),
                  (len(specs) * 4, len(specs) * 4 + 3, len(specs) * 4 + 2, len(specs) * 4 + 1)])
    slots.extend([0, len(specs) - 1])
    mesh = bpy.data.meshes.new(f"{props.type_name} mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for material in materials:
        mesh.materials.append(material)
    for polygon, slot in zip(mesh.polygons, slots):
        polygon.material_index = slot
    uv = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        for index, loop_index in enumerate(polygon.loop_indices):
            uv.data[loop_index].uv = ((0, 0), (1, 0), (1, 1), (0, 1))[index]
    obj = bpy.data.objects.new(f"Wall - {props.type_name}", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["ifc_type_guid"] = ifc_type.GlobalId
    obj["ifc_type_name"] = props.type_name
    obj["hemy_wall_preview"] = True
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def get_model_subcontext(model, identifier):
    import ifcopenshell.api

    parents = [c for c in model.by_type("IfcGeometricRepresentationContext") if c.ContextType == "Model"]
    parent = parents[0] if parents else ifcopenshell.api.run("context.add_context", model, context_type="Model")
    found = next((c for c in model.by_type("IfcGeometricRepresentationSubContext")
                  if c.ContextIdentifier == identifier and c.ParentContext == parent), None)
    return found or ifcopenshell.api.run("context.add_context", model, context_type="Model",
                                        context_identifier=identifier, target_view="MODEL_VIEW", parent=parent)


def create_wall_occurrence(model, props, wall_type, thickness, obj=None):
    import ifcopenshell.api
    import ifcopenshell.util.unit

    wall = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWall", name=f"{props.type_name} example")
    ifcopenshell.api.run("type.assign_type", model, related_objects=[wall], relating_type=wall_type)
    association = ifcopenshell.api.run("material.assign_material", model, products=[wall],
                                       type="IfcMaterialLayerSetUsage")
    usage = association.RelatingMaterial
    # The wall baseline runs along local X. Offset 0 puts the layer-set base
    # on that line; AXIS2/POSITIVE stacks the ordered layers toward local +Y.
    # The body mesh and IFC wall representation use that same Y range.
    usage.LayerSetDirection = "AXIS2"
    usage.DirectionSense = "POSITIVE"
    usage.OffsetFromReferenceLine = 0.0
    storeys = model.by_type("IfcBuildingStorey")
    if storeys:
        ifcopenshell.api.run("spatial.assign_container", model, products=[wall], relating_structure=storeys[0])
    body = get_model_subcontext(model, "Body")
    axis_context = get_model_subcontext(model, "Axis")
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    axis = ifcopenshell.api.run("geometry.add_axis_representation", model, context=axis_context,
                                axis=[(0.0, 0.0), (props.preview_length / scale, 0.0)])
    ifcopenshell.api.run("geometry.assign_representation", model, product=wall, representation=axis)
    shape = ifcopenshell.api.run("geometry.add_wall_representation", model, context=body,
                                 length=props.preview_length / scale, height=props.preview_height / scale,
                                 thickness=thickness / scale)
    ifcopenshell.api.run("geometry.assign_representation", model, product=wall, representation=shape)
    if obj is not None:
        import numpy
        from mathutils import Vector

        bounds = [tuple(vertex) for vertex in obj.bound_box]
        local_min = Vector(tuple(min(vertex[axis] for vertex in bounds) for axis in range(3)))
        matrix = numpy.array(obj.matrix_world, dtype=float)
        matrix[:3, 3] = obj.matrix_world @ local_min
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=wall, matrix=matrix, is_si=True)
    return wall


def show_photo_texture(context):
    for area in context.screen.areas if context.screen else []:
        if area.type == "VIEW_3D":
            shading = area.spaces.active.shading
            shading.type = "SOLID"
            shading.color_type = "TEXTURE"


def type_materials(type_guid, layer_count):
    """Find the packed Blender materials for a type, including older previews."""
    materials = {int(mat["hemy_layer_index"]): mat for mat in bpy.data.materials
                 if mat.get("hemy_ifc_type_guid") == type_guid and "hemy_layer_index" in mat}
    if len(materials) != layer_count:
        for obj in bpy.data.objects:
            if obj.get("hemy_wall_preview") and obj.get("ifc_type_guid") == type_guid:
                for index, material in enumerate(obj.data.materials):
                    if material:
                        material["hemy_ifc_type_guid"] = type_guid
                        material["hemy_layer_index"] = index
                        materials[index] = material
                break
    if any(index not in materials for index in range(layer_count)):
        return None
    return [materials[index] for index in range(layer_count)]


def apply_type_materials_to_object(obj, materials, thicknesses):
    """Put the finish on the exterior face and map layer slots to cut faces."""
    from bisect import bisect_right

    mesh = obj.data
    if mesh.users != 1 or not mesh.vertices or not mesh.polygons:
        return False
    ys = [vertex.co.y for vertex in mesh.vertices]
    xs = [vertex.co.x for vertex in mesh.vertices]
    zs = [vertex.co.z for vertex in mesh.vertices]
    min_y, max_y = min(ys), max(ys)
    if max_y - min_y <= 1e-9:
        return False
    boundaries = [0.0]
    for thickness in thicknesses:
        boundaries.append(boundaries[-1] + thickness)
    scale_y = (max_y - min_y) / boundaries[-1]
    tolerance = max((max_y - min_y) * 1e-4, 1e-6)
    type_guid = materials[0]["hemy_ifc_type_guid"]
    obj["hemy_material_type_guid"] = type_guid
    mesh.materials.clear()
    for material in materials:
        mesh.materials.append(material)
    uv = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    min_x, min_z = min(xs), min(zs)
    length, height = max(max(xs) - min_x, 1e-9), max(max(zs) - min_z, 1e-9)
    for polygon in mesh.polygons:
        vertices = [mesh.vertices[index].co for index in polygon.vertices]
        face_y = [vertex.y for vertex in vertices]
        if all(abs(y - min_y) <= tolerance for y in face_y):
            index = 0
        elif all(abs(y - max_y) <= tolerance for y in face_y):
            index = len(materials) - 1
        else:
            middle = sum(face_y) / len(face_y)
            index = max(0, min(len(materials) - 1,
                               bisect_right(boundaries, (middle - min_y) / scale_y) - 1))
        polygon.material_index = index
        for loop_index in polygon.loop_indices:
            vertex = mesh.vertices[mesh.loops[loop_index].vertex_index].co
            if abs(polygon.normal.y) >= abs(polygon.normal.x) and abs(polygon.normal.y) >= abs(polygon.normal.z):
                uv.data[loop_index].uv = ((vertex.x - min_x) / length, (vertex.z - min_z) / height)
            elif abs(polygon.normal.x) >= abs(polygon.normal.z):
                uv.data[loop_index].uv = ((vertex.y - min_y) / (max_y - min_y), (vertex.z - min_z) / height)
            else:
                uv.data[loop_index].uv = ((vertex.x - min_x) / length, (vertex.y - min_y) / (max_y - min_y))
    mesh.update()
    # Bonsai replaces the mesh when it cuts an opening for a door. Keep the
    # marker on the mesh so a replacement is styled even if it inherits slots.
    mesh["hemy_material_type_guid"] = type_guid
    return True


def sync_wall_materials(type_guid=None, force=False):
    """Style Bonsai-created occurrences of Hemy types in the current .blend."""
    import ifcopenshell.util.element
    import ifcopenshell.util.unit
    from . import bonsai_tool

    tool = bonsai_tool()
    model = tool.Ifc.get() if tool else None
    if model is None or bpy.context.scene is None:
        return 0
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    count = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or obj.get("hemy_wall_preview"):
            continue
        entity = tool.Ifc.get_entity(obj)
        if entity is None or not entity.is_a("IfcWall"):
            continue
        wall_type = ifcopenshell.util.element.get_type(entity)
        if wall_type is None or (type_guid and wall_type.GlobalId != type_guid):
            continue
        if (not force and obj.get("hemy_material_type_guid") == wall_type.GlobalId
                and obj.data.get("hemy_material_type_guid") == wall_type.GlobalId
                and obj.data.materials and obj.data.materials[0].get("hemy_ifc_type_guid") == wall_type.GlobalId):
            continue
        layer_sets = [rel.RelatingMaterial for rel in wall_type.HasAssociations
                      if rel.is_a("IfcRelAssociatesMaterial")
                      and rel.RelatingMaterial.is_a("IfcMaterialLayerSet")]
        if not layer_sets:
            continue
        layers = layer_sets[0].MaterialLayers
        materials = type_materials(wall_type.GlobalId, len(layers))
        if materials and apply_type_materials_to_object(
                obj, materials, [layer.LayerThickness * scale for layer in layers]):
            count += 1
    return count


_sync_pending = False


def _run_queued_sync():
    global _sync_pending
    _sync_pending = False
    try:
        sync_wall_materials()
    except Exception as error:
        print(f"Hemy wall material sync: {error}")
    return None


@persistent
def _queue_sync_after_object_change(scene, depsgraph):
    global _sync_pending
    # An opening can replace wall mesh data without an object update.
    if _sync_pending or not any(isinstance(update.id, (bpy.types.Object, bpy.types.Mesh))
                                for update in depsgraph.updates):
        return
    _sync_pending = True
    bpy.app.timers.register(_run_queued_sync, first_interval=0.4)


def create_wall_type(model, props, specs):
    import ifcopenshell.api
    import ifcopenshell.util.unit

    for existing in model.by_type("IfcWallType"):
        if existing.Name == props.type_name:
            raise ValueError(f"An IfcWallType named '{props.type_name}' already exists")
    wall_type = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWallType", name=props.type_name)
    layer_set = ifcopenshell.api.run("material.add_material_set", model, name=f"{props.type_name} layers", set_type="IfcMaterialLayerSet")
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    body = get_model_subcontext(model, "Body")
    for spec in specs:
        material = ifcopenshell.api.run("material.add_material", model, name=spec.material_name)
        layer = ifcopenshell.api.run("material.add_layer", model, layer_set=layer_set,
                                     material=material, name=spec.name)
        layer.LayerThickness = spec.thickness / unit_scale
        layer.IsVentilated = spec.is_ventilated
        # IFC material names describe construction; styles supply a reusable
        # display colour when Bonsai creates another wall from this type.
        style = ifcopenshell.api.run("style.add_style", model, name=spec.material_name)
        ifcopenshell.api.run("style.add_surface_style", model, style=style,
                             ifc_class="IfcSurfaceStyleShading", attributes={
                                 "SurfaceColour": {"Name": None, "Red": spec.color[0],
                                                   "Green": spec.color[1], "Blue": spec.color[2]},
                                 "Transparency": 1.0 - spec.color[3],
                             })
        ifcopenshell.api.run("style.assign_material_style", model, material=material,
                             style=style, context=body)
    ifcopenshell.api.run("material.assign_material", model, products=[wall_type], type="IfcMaterialLayerSet", material=layer_set)
    return wall_type


def suggest_from_image(path):
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ValueError("Set OPENAI_API_KEY in Blender's environment to use AI suggestions")
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
    image_data = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "model": os.environ.get("HEMY_VISION_MODEL", "gpt-4.1-mini"),
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Identify only the visible wall finish in this photo. Suggest a concise BIM wall type name and visible finish name. Do not guess hidden layers, thicknesses, fire rating, or performance. Return JSON only with keys type_name, surface_name, note."},
            {"type": "input_image", "image_url": f"data:{media};base64,{image_data}"},
        ]}],
        "text": {"format": {"type": "json_schema", "name": "wall_surface", "strict": True, "schema": {
            "type": "object", "properties": {"type_name": {"type": "string"}, "surface_name": {"type": "string"}, "note": {"type": "string"}},
            "required": ["type_name", "surface_name", "note"], "additionalProperties": False,
        }}},
    }
    request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"),
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    contents = [part.get("text", "") for item in result.get("output", [])
                for part in item.get("content", []) if part.get("type") == "output_text"]
    if not contents:
        raise ValueError("AI returned no wall suggestion")
    suggestion = json.loads("".join(contents))
    if not all(isinstance(suggestion.get(field), str) and suggestion[field].strip() for field in ("type_name", "surface_name")):
        raise ValueError("AI returned an incomplete wall suggestion")
    return suggestion


class HEMY_OT_pick_wall_image(Operator, ImportHelper):
    bl_idname = "hemy.pick_wall_image"
    bl_label = "Choose Wall Photo"
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.webp", options={"HIDDEN"})

    def execute(self, context):
        context.scene.hemy_wall_authoring.image_path = self.filepath
        return {"FINISHED"}


class HEMY_OT_paste_wall_image(Operator):
    bl_idname = "hemy.paste_wall_image"
    bl_label = "Paste Wall Photo"
    bl_description = "Paste an image from the Windows clipboard"

    def execute(self, context):
        if sys.platform != "win32":
            self.report({"ERROR"}, "Image paste currently requires Windows; use Choose Photo")
            return {"CANCELLED"}
        target = Path(tempfile.gettempdir()) / f"hemy_wall_clipboard_{uuid.uuid4().hex}.png"
        script = ("Add-Type -AssemblyName System.Windows.Forms; "
                  "Add-Type -AssemblyName System.Drawing; "
                  "if (-not [System.Windows.Forms.Clipboard]::ContainsImage()) { exit 2 }; "
                  "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
                  "$img.Save($env:HEMY_CLIPBOARD_TARGET, [System.Drawing.Imaging.ImageFormat]::Png); $img.Dispose()")
        env = dict(os.environ, HEMY_CLIPBOARD_TARGET=str(target))
        result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script], env=env, capture_output=True, text=True, timeout=20)
        if result.returncode or not target.is_file():
            self.report({"ERROR"}, "No image found on clipboard" if result.returncode == 2 else f"Could not paste image: {result.stderr[:160]}")
            return {"CANCELLED"}
        context.scene.hemy_wall_authoring.image_path = str(target)
        return {"FINISHED"}


class HEMY_OT_suggest_wall(Operator):
    bl_idname = "hemy.suggest_wall"
    bl_label = "Suggest with AI"
    bl_description = "Analyze the visible surface using the configured OpenAI API key"

    def execute(self, context):
        props = context.scene.hemy_wall_authoring
        try:
            suggestion = suggest_from_image(image_file(props))
            props.type_name = suggestion["type_name"].strip()[:100]
            if not props.layers:
                layer = props.layers.add()
                layer.layer_name = "Exterior finish"
                layer.thickness = 0.01
            props.layers[0].material_name = suggestion["surface_name"].strip()[:100]
            props.ai_note = suggestion.get("note", "")[:300]
            props.confirmed = False
        except Exception as error:
            self.report({"ERROR"}, f"AI suggestion failed: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Review the suggestion and enter the actual wall build-up")
        return {"FINISHED"}


class HEMY_OT_add_wall_layer(Operator):
    bl_idname = "hemy.add_wall_layer"
    bl_label = "Add Wall Layer"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.hemy_wall_authoring
        layer = props.layers.add()
        layer.layer_name = f"Layer {len(props.layers)}"
        props.active_layer = len(props.layers) - 1
        props.confirmed = False
        return {"FINISHED"}


class HEMY_OT_remove_wall_layer(Operator):
    bl_idname = "hemy.remove_wall_layer"
    bl_label = "Remove Wall Layer"
    bl_options = {"REGISTER", "UNDO"}
    index: IntProperty()

    def execute(self, context):
        props = context.scene.hemy_wall_authoring
        if not 0 <= self.index < len(props.layers):
            return {"CANCELLED"}
        props.layers.remove(self.index)
        props.active_layer = min(props.active_layer, max(0, len(props.layers) - 1))
        props.confirmed = False
        return {"FINISHED"}


class HEMY_OT_move_wall_layer(Operator):
    bl_idname = "hemy.move_wall_layer"
    bl_label = "Move Wall Layer"
    bl_options = {"REGISTER", "UNDO"}
    index: IntProperty()
    direction: IntProperty()

    def execute(self, context):
        props = context.scene.hemy_wall_authoring
        target = self.index + self.direction
        if not (0 <= self.index < len(props.layers) and 0 <= target < len(props.layers)):
            return {"CANCELLED"}
        props.layers.move(self.index, target)
        props.active_layer = target
        props.confirmed = False
        return {"FINISHED"}


class HEMY_OT_preset_wall_layers(Operator):
    bl_idname = "hemy.preset_wall_layers"
    bl_label = "Start with Three Layers"
    bl_description = "Add editable exterior finish, core, and interior finish rows"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.hemy_wall_authoring
        props.layers.clear()
        for role, material, thickness, color in (
            ("Exterior finish", "Exterior finish", 0.015, (0.72, 0.48, 0.35, 1.0)),
            ("Core", "Concrete", 0.20, (0.55, 0.57, 0.59, 1.0)),
            ("Interior finish", "Gypsum", 0.0125, (0.9, 0.88, 0.83, 1.0)),
        ):
            layer = props.layers.add()
            layer.layer_name = role
            layer.material_name = material
            layer.thickness = thickness
            layer.color = color
        props.active_layer = 0
        props.confirmed = False
        return {"FINISHED"}


class HEMY_OT_create_wall_type(Operator):
    bl_idname = "hemy.create_wall_type"
    bl_label = "Create IFC Wall Type and Wall"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import bonsai_tool
        tool = bonsai_tool()
        if tool and tool.Ifc.get() is not None and tool.Ifc.__module__.startswith("bonsai"):
            from bonsai.bim.ifc import IfcStore
            return IfcStore.execute_ifc_operator(self, context)
        return self._execute(context)

    def _execute(self, context):
        from . import bonsai_tool
        props = context.scene.hemy_wall_authoring
        tool = bonsai_tool()
        model = tool.Ifc.get() if tool else None
        if model is None:
            self.report({"ERROR"}, "Enable Bonsai and open an IFC project")
            return {"CANCELLED"}
        try:
            if model.schema != "IFC4":
                raise ValueError("This wall-layer builder requires an IFC4 project")
            path = image_file(props)
            if not props.type_name.strip():
                raise ValueError("Enter a wall type name")
            specs = collect_layers(props)
            if not props.confirmed:
                raise ValueError("Review the wall build-up and check the confirmation box")
            wall_type = create_wall_type(model, props, specs)
            wall = create_wall_occurrence(model, props, wall_type, sum(spec.thickness for spec in specs))
            materials = [make_material(spec, path if index == 0 else None,
                                       type_guid=wall_type.GlobalId, layer_index=index)
                         for index, spec in enumerate(specs)]
            obj = make_preview(props, wall_type, specs, materials)
            if hasattr(tool.Ifc, "link"):
                tool.Ifc.link(wall, obj)
            show_photo_texture(context)
        except Exception as error:
            self.report({"ERROR"}, f"Could not create wall type: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Created {props.type_name}. Save the IFC and .blend files")
        return {"FINISHED"}


class HEMY_OT_show_photo_texture(Operator):
    bl_idname = "hemy.show_photo_texture"
    bl_label = "Show Photo Texture"
    bl_description = "Display the selected wall's image texture using Solid textured shading"

    def execute(self, context):
        obj = context.active_object
        if obj is None or not obj.get("hemy_wall_preview"):
            self.report({"ERROR"}, "Select a wall created by this panel")
            return {"CANCELLED"}
        show_photo_texture(context)
        self.report({"INFO"}, "Solid textured shading enabled for 3D views")
        return {"FINISHED"}


class HEMY_OT_apply_finish_to_type_walls(Operator):
    bl_idname = "hemy.apply_finish_to_type_walls"
    bl_label = "Apply Finish to Walls of This Type"
    bl_description = "Apply the saved photo and layer colors to Bonsai walls drawn from the selected wall type"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import ifcopenshell.util.element
        from . import bonsai_tool

        tool = bonsai_tool()
        model = tool.Ifc.get() if tool else None
        if model is None:
            self.report({"ERROR"}, "Open the IFC project containing the wall type")
            return {"CANCELLED"}
        obj = context.active_object
        entity = tool.Ifc.get_entity(obj) if obj else None
        wall_type = (ifcopenshell.util.element.get_type(entity) if entity and entity.is_a("IfcWall")
                     else None)
        if wall_type is None and obj and obj.get("ifc_type_guid"):
            try:
                wall_type = model.by_guid(obj["ifc_type_guid"])
            except RuntimeError:
                pass
        if wall_type is None or not wall_type.is_a("IfcWallType"):
            self.report({"ERROR"}, "Select a wall of the Hemy type or its original example wall")
            return {"CANCELLED"}
        try:
            count = sync_wall_materials(type_guid=wall_type.GlobalId, force=True)
            show_photo_texture(context)
        except Exception as error:
            self.report({"ERROR"}, f"Could not apply wall finish: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Applied photo finish to {count} wall(s) of {wall_type.Name}")
        return {"FINISHED"}


class HEMY_OT_link_existing_preview(Operator):
    bl_idname = "hemy.link_existing_wall_preview"
    bl_label = "Link Older Preview to IFC Wall"
    bl_description = "Make a pre-0.3.1 photo preview into an IFC wall occurrence of its existing type"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import bonsai_tool
        tool = bonsai_tool()
        if tool and tool.Ifc.get() is not None and tool.Ifc.__module__.startswith("bonsai"):
            from bonsai.bim.ifc import IfcStore
            return IfcStore.execute_ifc_operator(self, context)
        return self._execute(context)

    def _execute(self, context):
        from . import bonsai_tool
        tool = bonsai_tool()
        model = tool.Ifc.get() if tool else None
        obj = context.active_object
        try:
            if model is None:
                raise ValueError("Open the matching IFC project in Bonsai")
            if obj is None or not obj.get("hemy_wall_preview"):
                raise ValueError("Select a wall preview made by this add-on")
            if tool.Ifc.get_entity(obj) is not None:
                raise ValueError("This preview is already linked to IFC")
            wall_type = model.by_guid(obj.get("ifc_type_guid", ""))
            if wall_type is None or not wall_type.is_a("IfcWallType"):
                raise ValueError("The matching IfcWallType is not in the open IFC project")
            size = obj.dimensions
            values = SimpleNamespace(type_name=wall_type.Name, preview_length=size.x,
                                     preview_height=size.z)
            wall = create_wall_occurrence(model, values, wall_type, size.y, obj=obj)
            tool.Ifc.link(wall, obj)
            show_photo_texture(context)
        except Exception as error:
            self.report({"ERROR"}, f"Could not link preview: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Preview linked to an IfcWall. Save the IFC and .blend files")
        return {"FINISHED"}


class HEMY_OT_export_wall_usdz(Operator, ExportHelper):
    bl_idname = "hemy.export_wall_usdz"
    bl_label = "Export Preview to Omniverse USDZ"
    filename_ext = ".usdz"
    filter_glob: StringProperty(default="*.usdz", options={"HIDDEN"})

    def invoke(self, context, event):
        obj = context.active_object
        if obj is None or not obj.get("hemy_wall_preview"):
            self.report({"ERROR"}, "Select a generated wall preview first")
            return {"CANCELLED"}
        self.filepath = bpy.path.clean_name(obj.name) + ".usdz"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        obj = context.active_object
        if obj is None or not obj.get("hemy_wall_preview"):
            self.report({"ERROR"}, "Select a generated wall preview first")
            return {"CANCELLED"}
        try:
            filepath = str(Path(self.filepath).with_suffix(".usdz"))
            supported = bpy.ops.wm.usd_export.get_rna_type().properties.keys()
            options = {"filepath": filepath, "selected_objects_only": True,
                       "export_materials": True, "export_uvmaps": True,
                       "generate_preview_surface": True, "export_custom_properties": True,
                       "relative_paths": True}
            if "export_textures_mode" in supported:
                options["export_textures_mode"] = "NEW"
            elif "export_textures" in supported:
                options["export_textures"] = True
            result = bpy.ops.wm.usd_export(**{key: value for key, value in options.items() if key in supported})
            if result != {"FINISHED"}:
                raise ValueError(str(result))
        except Exception as error:
            self.report({"ERROR"}, f"USDZ export failed: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {filepath}")
        return {"FINISHED"}


class HEMY_PT_wall_authoring(Panel):
    bl_idname = "HEMY_PT_wall_authoring"
    bl_label = "Create Wall Type from Photo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hemy IFC"
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        props = context.scene.hemy_wall_authoring
        row = layout.row(align=True)
        row.operator("hemy.pick_wall_image", text="Choose Photo", icon="FILE_IMAGE")
        row.operator("hemy.paste_wall_image", text="Paste", icon="PASTEDOWN")
        layout.prop(props, "image_path", text="Image")
        layout.operator("hemy.suggest_wall", text="Suggest with AI", icon="LIGHT")
        if props.ai_note:
            layout.label(text=props.ai_note[:45], icon="INFO")
        layout.prop(props, "type_name")
        layout.label(text="Layers: exterior to interior (+Y)")
        row = layout.row(align=True)
        row.operator("hemy.add_wall_layer", text="Add layer", icon="ADD")
        row.operator("hemy.preset_wall_layers", text="Three-layer example", icon="PRESET")
        total = 0.0
        for index, layer in enumerate(props.layers):
            box = layout.box()
            row = box.row(align=True)
            row.label(text=f"{index + 1}. {layer.layer_name}")
            up = row.operator("hemy.move_wall_layer", text="", icon="TRIA_UP")
            up.index, up.direction = index, -1
            down = row.operator("hemy.move_wall_layer", text="", icon="TRIA_DOWN")
            down.index, down.direction = index, 1
            remove = row.operator("hemy.remove_wall_layer", text="", icon="X")
            remove.index = index
            box.prop(layer, "layer_name")
            box.prop(layer, "material_name")
            box.prop(layer, "thickness")
            box.prop(layer, "is_ventilated")
            box.prop(layer, "color")
            total += layer.thickness
        layout.label(text=f"Total thickness: {total:.3f} m")
        layout.label(text="Photo texture applies to layer 1")
        layout.prop(props, "preview_length")
        layout.prop(props, "preview_height")
        layout.prop(props, "confirmed")
        layout.operator("hemy.create_wall_type", icon="MOD_BUILD")
        layout.operator("hemy.show_photo_texture", icon="SHADING_TEXTURE")
        layout.operator("hemy.apply_finish_to_type_walls", icon="MATERIAL")
        layout.operator("hemy.link_existing_wall_preview", icon="LINKED")
        layout.operator("hemy.export_wall_usdz", icon="EXPORT")


CLASSES = (HEMY_PG_wall_layer, HEMY_PG_wall_authoring,
           HEMY_OT_pick_wall_image, HEMY_OT_paste_wall_image,
           HEMY_OT_suggest_wall, HEMY_OT_add_wall_layer, HEMY_OT_remove_wall_layer,
           HEMY_OT_move_wall_layer, HEMY_OT_preset_wall_layers,
           HEMY_OT_create_wall_type, HEMY_OT_show_photo_texture,
           HEMY_OT_apply_finish_to_type_walls,
           HEMY_OT_link_existing_preview,
           HEMY_OT_export_wall_usdz,
           HEMY_PT_wall_authoring)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hemy_wall_authoring = PointerProperty(type=HEMY_PG_wall_authoring)
    if _queue_sync_after_object_change not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_queue_sync_after_object_change)


def unregister():
    if _queue_sync_after_object_change in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_queue_sync_after_object_change)
    if bpy.app.timers.is_registered(_run_queued_sync):
        bpy.app.timers.unregister(_run_queued_sync)
    if hasattr(bpy.types.Scene, "hemy_wall_authoring"):
        del bpy.types.Scene.hemy_wall_authoring
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
