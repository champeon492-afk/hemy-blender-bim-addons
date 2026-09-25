"""Exercise wall authoring against the installed Bonsai transaction layer."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy
import ifcopenshell.api
import bonsai.tool as tool
import hemy_ifc_panel as panel

assert panel.bl_info["version"] >= (0, 3, 0), panel.__file__

model = ifcopenshell.api.run("project.create_file", version="IFC4")
ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="Bonsai wall QA")
millimetres = ifcopenshell.api.run("unit.add_si_unit", model, unit_type="LENGTHUNIT", prefix="MILLI")
ifcopenshell.api.run("unit.assign_unit", model, units=[millimetres])
tool.Ifc.set(model)
props = bpy.context.scene.hemy_wall_authoring
props.image_path = str(ROOT / "qa" / "wall-authoring-sample.png")
props.type_name = "QA Bonsai wall"
assert bpy.ops.hemy.preset_wall_layers() == {"FINISHED"}
props.layers[0].material_name = "QA finish"
props.layers[1].material_name = "QA core"
props.layers[2].material_name = "QA interior"
props.layers[0].thickness = 0.015
props.layers[1].thickness = 0.2
props.layers[2].thickness = 0.0125
props.confirmed = True
assert bpy.ops.hemy.create_wall_type() == {"FINISHED"}
wall_type = model.by_type("IfcWallType")[0]
wall = model.by_type("IfcWall")[0]
assert tool.Ifc.get_entity(bpy.context.active_object) == wall
assert wall.IsTypedBy[0].RelatingType == wall_type
assert bpy.context.active_object.data.materials[0].node_tree.nodes.get("Image Texture").image.packed_file
layers = wall_type.HasAssociations[0].RelatingMaterial.MaterialLayers
assert [round(layer.LayerThickness, 1) for layer in layers] == [15, 200, 12.5]
usage = wall.HasAssociations[0].RelatingMaterial
assert usage.is_a("IfcMaterialLayerSetUsage")
assert usage.ForLayerSet == wall_type.HasAssociations[0].RelatingMaterial
assert abs(bpy.context.active_object.dimensions.y - 0.2275) < 1e-6
assert len(model.by_type("IfcSurfaceStyle")) == 3

drawn = panel.wall_authoring.create_wall_occurrence(model, props, wall_type, 0.2275)
bpy.ops.mesh.primitive_cube_add(size=1)
drawn_obj = bpy.context.object
drawn_obj.dimensions = (2.0, 0.2275, 2.8)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
tool.Ifc.link(drawn, drawn_obj)
assert panel.wall_authoring.sync_wall_materials() == 1
assert len(drawn_obj.data.materials) == 3
assert drawn_obj.data.materials[0].node_tree.nodes.get("Image Texture").image.packed_file
model.write(str(ROOT / "qa" / "wall-bonsai-sample.ifc"))
print("HEMY_BONSAI_WALL_PASSED", wall_type.GlobalId)
