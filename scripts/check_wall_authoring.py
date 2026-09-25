"""Integration check for wall authoring and packaged Omniverse preview."""

from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy
import ifcopenshell.api
import ifcopenshell.validate
import hemy_ifc_panel as panel

panel.register()
model = ifcopenshell.api.run("project.create_file", version="IFC4")
ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="Wall Authoring QA")
metres = ifcopenshell.api.run("unit.add_si_unit", model, unit_type="LENGTHUNIT")
ifcopenshell.api.run("unit.assign_unit", model, units=[metres])

class FakeIfc:
    linked = {}

    @staticmethod
    def get():
        return model

    @staticmethod
    def get_entity(obj):
        return FakeIfc.linked.get(obj)

    @staticmethod
    def link(entity, obj):
        FakeIfc.linked[obj] = entity

class FakeTool:
    Ifc = FakeIfc

panel.bonsai_tool = lambda: FakeTool
props = bpy.context.scene.hemy_wall_authoring
image_path = ROOT / "qa" / "wall-authoring-sample.png"
image_path.parent.mkdir(exist_ok=True)
image = bpy.data.images.new("QA finish image", width=8, height=8)
image.pixels[:] = [0.7, 0.25, 0.1, 1.0] * 64
image.filepath_raw = str(image_path)
image.file_format = "PNG"
image.save()

props.image_path = str(image_path)
props.type_name = "QA brick wall"
assert bpy.ops.hemy.preset_wall_layers() == {"FINISHED"}
assert bpy.ops.hemy.add_wall_layer() == {"FINISHED"}
assert len(props.layers) == 4
assert bpy.ops.hemy.remove_wall_layer(index=3) == {"FINISHED"}
assert bpy.ops.hemy.move_wall_layer(index=2, direction=-1) == {"FINISHED"}
assert bpy.ops.hemy.move_wall_layer(index=1, direction=1) == {"FINISHED"}
assert [layer.layer_name for layer in props.layers] == ["Exterior finish", "Core", "Interior finish"]
props.layers[0].material_name = "QA brick finish"
props.layers[1].material_name = "QA ventilated cavity"
props.layers[1].is_ventilated = True
props.layers[2].material_name = "QA gypsum finish"
props.layers[0].thickness = 0.015
props.layers[1].thickness = 0.2
props.layers[2].thickness = 0.0125
try:
    bpy.ops.hemy.create_wall_type()
    raise AssertionError("Creation should require explicit confirmation")
except RuntimeError as error:
    assert "confirmation box" in str(error)
props.confirmed = True
assert bpy.ops.hemy.create_wall_type() == {"FINISHED"}
wall_type = model.by_type("IfcWallType")[0]
assert wall_type.Name == props.type_name
wall = model.by_type("IfcWall")[0]
assert wall.IsTypedBy[0].RelatingType == wall_type
assert wall.Representation is not None
layer_set = wall_type.HasAssociations[0].RelatingMaterial
assert layer_set.is_a("IfcMaterialLayerSet")
assert [round(layer.LayerThickness, 4) for layer in layer_set.MaterialLayers] == [0.015, 0.2, 0.0125]
assert [layer.Material.Name for layer in layer_set.MaterialLayers] == [item.material_name for item in props.layers]
assert [layer.Name for layer in layer_set.MaterialLayers] == [item.layer_name for item in props.layers]
assert [layer.IsVentilated for layer in layer_set.MaterialLayers] == [False, True, False]
assert len(model.by_type("IfcSurfaceStyle")) == len(props.layers)
assert all(layer.Material.HasRepresentation for layer in layer_set.MaterialLayers)
usage = wall.HasAssociations[0].RelatingMaterial
assert usage.is_a("IfcMaterialLayerSetUsage") and usage.ForLayerSet == layer_set
assert usage.LayerSetDirection == "AXIS2" and usage.DirectionSense == "POSITIVE"
assert usage.OffsetFromReferenceLine == 0.0
assert {r.RepresentationIdentifier for r in wall.Representation.Representations} == {"Axis", "Body"}
obj = bpy.context.active_object
assert obj.get("ifc_type_guid") == wall_type.GlobalId
assert obj.data.materials[0].node_tree.nodes.get("Image Texture").image.packed_file
assert obj.data.uv_layers
assert len(obj.data.materials) == 3
assert {polygon.material_index for polygon in obj.data.polygons} == {0, 1, 2}
assert abs(obj.dimensions.y - sum(item.thickness for item in props.layers)) < 1e-6
assert all(material.get("hemy_ifc_type_guid") == wall_type.GlobalId
           for material in obj.data.materials)
assert bpy.ops.hemy.show_photo_texture() == {"FINISHED"}
assert all(area.spaces.active.shading.color_type == "TEXTURE" for area in bpy.context.screen.areas if area.type == "VIEW_3D")

# A wall drawn later by Bonsai has the type relationship but initially no
# photo material. The sync action must pick up the saved shader from the type.
drawn = panel.wall_authoring.create_wall_occurrence(
    model, props, wall_type, sum(item.thickness for item in props.layers))
bpy.ops.mesh.primitive_cube_add(size=1)
drawn_object = bpy.context.object
drawn_object.dimensions = (2.0, 0.2275, 2.8)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
FakeIfc.link(drawn, drawn_object)
assert not drawn_object.data.materials
# Simulate a type created by v0.4.0, before materials carried type tags.
for material in obj.data.materials:
    del material["hemy_ifc_type_guid"]
    del material["hemy_layer_index"]
assert bpy.ops.hemy.apply_finish_to_type_walls() == {"FINISHED"}
assert len(drawn_object.data.materials) == len(props.layers)
assert drawn_object.data.materials[0].node_tree.nodes.get("Image Texture").image.packed_file
assert drawn_object.data.uv_layers
assert drawn_object.get("hemy_material_type_guid") == wall_type.GlobalId
assert panel.wall_authoring.sync_wall_materials() == 0

drawn_again = panel.wall_authoring.create_wall_occurrence(
    model, props, wall_type, sum(item.thickness for item in props.layers))
bpy.ops.mesh.primitive_cube_add(size=1)
automatic_object = bpy.context.object
automatic_object.dimensions = (2.0, 0.2275, 2.8)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
FakeIfc.link(drawn_again, automatic_object)
panel.wall_authoring._run_queued_sync()  # callback scheduled after Bonsai object updates
assert automatic_object.data.materials[0].get("hemy_ifc_type_guid") == wall_type.GlobalId

# Upgrade a centered preview from the previous add-on version without duplicating its type.
bpy.ops.mesh.primitive_cube_add(size=1)
old_preview = bpy.context.object
old_preview.dimensions = (2.0, 0.2275, 2.8)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
old_preview["hemy_wall_preview"] = True
old_preview["ifc_type_guid"] = wall_type.GlobalId
old_preview.data.materials.append(obj.data.materials[0])
assert bpy.ops.hemy.link_existing_wall_preview() == {"FINISHED"}
upgraded = FakeIfc.get_entity(old_preview)
assert upgraded.is_a("IfcWall") and upgraded.IsTypedBy[0].RelatingType == wall_type
assert len(model.by_type("IfcWallType")) == 1
assert upgraded.ObjectPlacement is not None
logger = ifcopenshell.validate.json_logger()
ifcopenshell.validate.validate(model, logger)
assert not logger.statements, logger.statements

for selected in bpy.context.selected_objects:
    selected.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj

target = ROOT / "qa" / "wall-authoring-sample.usdz"
assert bpy.ops.hemy.export_wall_usdz(filepath=str(target)) == {"FINISHED"}
with zipfile.ZipFile(target) as archive:
    names = archive.namelist()
    assert any(name.lower().endswith(".png") for name in names), names
    assert any(name.lower().endswith((".usd", ".usdc", ".usda")) for name in names), names
model.write(str(ROOT / "qa" / "wall-authoring-sample.ifc"))
panel.unregister()
print("HEMY_WALL_AUTHORING_PASSED", wall_type.GlobalId, names)
