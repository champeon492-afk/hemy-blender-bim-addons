"""Read representative elements from a real IFC file without opening Bonsai."""
from pathlib import Path
import sys
import json
import time

import ifcopenshell

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from hemy_ifc_panel.ifc_data import read_element

args = sys.argv[sys.argv.index("--") + 1:]
started = time.monotonic()
model = ifcopenshell.open(args[0])
report = {"schema": model.schema, "elements": []}
for ifc_class in ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcCovering", "IfcBuildingElementProxy", "IfcRailing", "IfcBuildingStorey"):
    for entity in model.by_type(ifc_class)[:3]:
        data = read_element(entity, model)
        assert data["id"] == entity.id()
        report["elements"].append({"class": entity.is_a(), "id": entity.id(),
                                    "psets": len(data["psets"]), "qtos": len(data["qsets"])})
report["seconds"] = round(time.monotonic() - started, 2)
(root / "qa" / "ifc-model-read-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("HEMY_REAL_MODEL_READ_PASSED", json.dumps(report), flush=True)
