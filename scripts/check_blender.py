"""Run with blender --background --factory-startup --python scripts/check_blender.py."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import hemy_ifc_panel as panel
import ifcopenshell
import ifcopenshell.api
from hemy_ifc_panel.ifc_data import read_element

# The extension entry point has no .tool submodule: tools live in a wheel.
extension_context = SimpleNamespace(context=SimpleNamespace(preferences=SimpleNamespace(
    addons=[SimpleNamespace(module="bl_ext.user_default.bonsai")]
)))
tool_sentinel = SimpleNamespace(Ifc=object())
with patch.object(panel, "bpy", extension_context), patch.object(panel.importlib, "import_module", return_value=tool_sentinel) as importer:
    assert panel.bonsai_tool() is tool_sentinel
    importer.assert_called_once_with("bonsai.tool")

def legacy_layout(name):
    if name == "bonsai.tool":
        raise ModuleNotFoundError("No module named bonsai", name="bonsai")
    assert name == "bl_ext.user_default.bonsai.tool"
    return tool_sentinel
with patch.object(panel, "bpy", extension_context), patch.object(panel.importlib, "import_module", side_effect=legacy_layout):
    assert panel.bonsai_tool() is tool_sentinel
with patch.object(panel, "bpy", extension_context), patch.object(panel.importlib, "import_module", side_effect=ModuleNotFoundError("Missing dependency", name="a_broken_dependency")):
    try:
        panel.bonsai_tool()
        raise AssertionError("A broken tool dependency was hidden")
    except ModuleNotFoundError as error:
        assert error.name == "a_broken_dependency"

panel.register()
assert panel.snapshot(bpy.context)[0] is None  # Bonsai is disabled in factory startup.
# Saved sample-mode values from earlier versions must never override selection.
bpy.context.scene.hemy_ifc_panel["source"] = 0
bpy.context.scene.hemy_ifc_panel["demo_element"] = 1
assert panel.snapshot(bpy.context)[0] is None

model = ifcopenshell.api.run("project.create_file", version="IFC4")
project = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="Hemy QA")
metres = ifcopenshell.api.run("unit.add_si_unit", model, unit_type="LENGTHUNIT")
area = ifcopenshell.api.run("unit.add_si_unit", model, unit_type="AREAUNIT")
ifcopenshell.api.run("unit.assign_unit", model, units=[metres, area])
site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite", name="Sørenga 12")
building = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name="Building A")
storey = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name="Level 02")
for parent, child in ((project,site),(site,building),(building,storey)):
    ifcopenshell.api.run("aggregate.assign_object", model, products=[child], relating_object=parent)
wall = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWall", name="QA Wall")
door = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcDoor", name="QA Door")
ifcopenshell.api.run("spatial.assign_container", model, products=[wall], relating_structure=storey)
wall_type = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWallType", name="Concrete Type")
ifcopenshell.api.run("type.assign_type", model, related_objects=[wall], relating_type=wall_type)
type_set = ifcopenshell.api.run("pset.add_pset", model, product=wall_type, name="Pset_WallCommon")
ifcopenshell.api.run("pset.edit_pset", model, pset=type_set, properties={"FireRating":"EI90"})
occ_set = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_WallCommon")
ifcopenshell.api.run("pset.edit_pset", model, pset=occ_set, properties={"FireRating":"EI60", "IsExternal":False, "LoadBearing":True})
qto = ifcopenshell.api.run("pset.add_qto", model, product=wall, name="Qto_WallBaseQuantities")
ifcopenshell.api.run("pset.edit_qto", model, qto=qto, properties={"Length":6.2,"NetSideArea":18.6})
material = ifcopenshell.api.run("material.add_material", model, name="Concrete C30/37", category="Concrete")
ifcopenshell.api.run("material.assign_material", model, products=[wall], material=material)
classification = ifcopenshell.api.run("classification.add_classification", model, classification="Uniclass 2015")
ifcopenshell.api.run("classification.add_reference", model, products=[wall], classification=classification, identification="Ss_25_10_30", name="Concrete walls")
millimetres = ifcopenshell.api.run("unit.add_si_unit", model, unit_type="LENGTHUNIT", prefix="MILLI")
custom = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Hemy_Dimensions")
custom.HasProperties = [model.create_entity("IfcPropertySingleValue", Name="Thickness", NominalValue=model.create_entity("IfcLengthMeasure",200.), Unit=millimetres)]

result = read_element(wall, model)
occurrence = next(s for s in result["psets"] if s["name"] == "Pset_WallCommon" and s["source"] == "Occurrence")
assigned_type = next(s for s in result["psets"] if s["source"] == "Assigned type")
assert dict(occurrence["rows"])["IsExternal"] is False
assert dict(occurrence["rows"])["FireRating"] == "EI60"
assert dict(assigned_type["rows"])["FireRating"] == "EI90"
assert dict(result["qsets"][0]["rows"])["Length"] == "6.2 m"
assert dict(result["qsets"][0]["rows"])["NetSideArea"] == "18.6 m2"
assert dict(next(s for s in result["psets"] if s["name"] == "Hemy_Dimensions")["rows"])["Thickness"] == "200 mm"
assert [row[1] for row in result["spatial"]] == ["Hemy QA","Sørenga 12","Building A","Level 02"]
assert result["type"] == "Concrete Type"
assert ["Code","Ss_25_10_30"] in result["classification"]
assert ["Material 1","Concrete C30/37"] in result["materials"]

# The boundary resolves the active object through Bonsai's tool API.
wall_object = bpy.context.active_object
door_object = bpy.data.objects.new("QA Door", None)
bpy.context.scene.collection.objects.link(door_object)
second_wall_object = bpy.data.objects.new("QA Wall 2", None)
bpy.context.scene.collection.objects.link(second_wall_object)
bpy.context.view_layer.update()
entities = {wall_object: wall, second_wall_object: wall, door_object: door}

def select_only(obj):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    bpy.context.view_layer.objects.active = obj
    if obj is not None:
        obj.select_set(True)

class FakeIfc:
    @staticmethod
    def get(): return model
    @staticmethod
    def get_entity(obj): return entities.get(obj)
class FakeTool:
    Ifc = FakeIfc
original_tool = panel.bonsai_tool
panel.bonsai_tool = lambda: FakeTool
groups = panel.class_objects(bpy.context, FakeTool)
assert set(groups) == {"IfcWall", "IfcDoor"}
assert len(groups["IfcWall"]) == 2
second_wall_object.hide_set(True, view_layer=bpy.context.view_layer)
assert bpy.ops.hemy.toggle_ifc_class_visibility(ifc_class="IfcWall") == {"FINISHED"}
assert wall_object.hide_get(view_layer=bpy.context.view_layer)
assert second_wall_object.hide_get(view_layer=bpy.context.view_layer)
assert not door_object.hide_get(view_layer=bpy.context.view_layer)
assert bpy.ops.hemy.toggle_ifc_class_visibility(ifc_class="IfcWall") == {"FINISHED"}
assert not wall_object.hide_get(view_layer=bpy.context.view_layer)
assert not second_wall_object.hide_get(view_layer=bpy.context.view_layer)
assert not door_object.hide_get(view_layer=bpy.context.view_layer)
select_only(wall_object)
assert panel.snapshot(bpy.context)[0]["name"] == "QA Wall"
select_only(door_object)
assert panel.snapshot(bpy.context)[0]["name"] == "QA Door"
select_only(wall_object)
assert panel.snapshot(bpy.context)[0]["name"] == "QA Wall"
bpy.context.active_object.select_set(False)
assert panel.snapshot(bpy.context)[0] is None  # Active does not always mean selected.
select_only(None)
assert panel.snapshot(bpy.context)[0] is None
select_only(bpy.data.objects["Camera"])
bpy.context.active_object["hemy_panel_demo_id"] = "wall"
assert panel.snapshot(bpy.context)[0] is None  # Old sample meshes have no IFC data.
select_only(wall_object)
with patch.object(FakeIfc, "get", return_value=None):
    assert panel.snapshot(bpy.context)[0] is None
panel.bonsai_tool = lambda: None
assert panel.snapshot(bpy.context)[0] is None
panel.bonsai_tool = lambda: FakeTool
out = ROOT / "qa"
out.mkdir(exist_ok=True)
assert bpy.ops.hemy.export_element_json(filepath=str(out/"selected-export.json")) == {"FINISHED"}
exported = json.loads((out/"selected-export.json").read_text(encoding="utf-8"))
assert exported["source"] == "IFC4 / Selected element"
assert exported["element"]["globalId"] == wall.GlobalId
panel.bonsai_tool = original_tool
(out/"ifc-adapter-result.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
model.write(str(out/"adapter-fixture.ifc"))
# Check that the chosen native icon identifiers exist on the installed Blender.
available_icons = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys()
for _,_,icon,_ in panel.groups_for(result):
    assert icon in available_icons, icon
panel.unregister()
assert not hasattr(bpy.types.Scene,"hemy_ifc_panel")
panel.register()
panel.unregister()
print("HEMY_CHECKS_PASSED: registration, IFC class visibility, automatic selection changes, legacy mode ignored, IFC units, booleans, occurrence/type sources, material, classification, spatial path, empty/non-IFC selection, export, unregister/re-register")
