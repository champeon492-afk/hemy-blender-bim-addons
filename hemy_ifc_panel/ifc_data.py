"""Read-only IFC adapter. Values keep their explicit or project units."""

import json


def display_value(value):
    if value is None:
        return "Not assigned"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return format(value, ".10g")
    if hasattr(value, "is_a"):
        return getattr(value, "Name", None) or f"{value.is_a()} #{value.id()}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=display_value)
    if isinstance(value, (list, tuple)):
        return ", ".join(display_value(item) for item in value)
    return str(value)


def unit_label(unit, unit_util):
    if unit.is_a("IfcDerivedUnit"):
        return " · ".join(
            unit_label(part.Unit, unit_util) + (f"^{part.Exponent}" if part.Exponent != 1 else "")
            for part in unit.Elements
        )
    if unit.is_a("IfcMonetaryUnit"):
        return unit.Currency
    return unit_util.get_unit_symbol(unit)


def read_sets(entity, ifc_file, *, quantities=False, source="Occurrence"):
    from ifcopenshell.util import element as eu, unit as uu

    result = []
    for name, properties in eu.get_psets(
        entity, psets_only=not quantities, qtos_only=quantities,
        should_inherit=False, verbose=True,
    ).items():
        rows = []
        for key, detail in properties.items():
            if key == "id":
                continue
            # Complex properties stay inspectable as structured text.
            value = detail.get("value", detail) if isinstance(detail, dict) else detail
            formatted = value if isinstance(value, bool) else display_value(value)
            prop_id = detail.get("id") if isinstance(detail, dict) else None
            if prop_id and value is not None and not isinstance(value, bool):
                prop = ifc_file.by_id(prop_id)
                unit = uu.get_property_unit(prop, ifc_file)
                if unit:
                    formatted = f"{formatted} {unit_label(unit, uu)}"
            rows.append([key, formatted])
        result.append({"name": name, "source": source, "rows": rows})
    return result


def read_element(entity, ifc_file):
    from ifcopenshell.util import element as eu, classification as cu

    element_type = eu.get_type(entity)
    attributes = []
    for key, value in entity.get_info().items():
        if key in {"id", "type", "GlobalId", "OwnerHistory", "ObjectPlacement", "Representation"}:
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            attributes.append([key, value if isinstance(value, bool) else display_value(value)])

    psets = read_sets(entity, ifc_file)
    qsets = read_sets(entity, ifc_file, quantities=True)
    if element_type and element_type != entity:
        # Separate the source so an occurrence override is never mislabeled as inherited.
        psets += read_sets(element_type, ifc_file, source="Assigned type")
        qsets += read_sets(element_type, ifc_file, quantities=True, source="Assigned type")

    materials = []
    for index, material in enumerate(eu.get_materials(entity), 1):
        materials.append([f"Material {index}", display_value(material.Name)])
        category = getattr(material, "Category", None)
        if category:
            materials.append([f"Category {index}", category])

    classifications = []
    for reference in sorted(cu.get_references(entity), key=lambda ref: ref.id()):
        system = cu.get_classification(reference)
        code = getattr(reference, "Identification", None) or getattr(reference, "ItemReference", None)
        classifications.extend([
            ["System", display_value(system.Name if system else None)],
            ["Code", display_value(code)], ["Description", display_value(reference.Name)],
        ])

    spatial = []
    parent = eu.get_parent(entity)
    seen = {entity.id()}
    while parent and parent.id() not in seen:
        seen.add(parent.id())
        spatial.append([parent.is_a().removeprefix("Ifc"), display_value(parent.Name)])
        parent = eu.get_parent(parent)
    spatial.reverse()
    return {
        "id": entity.id(), "name": entity.Name or f"{entity.is_a()} #{entity.id()}",
        "ifcClass": entity.is_a(), "globalId": getattr(entity, "GlobalId", ""),
        "description": getattr(entity, "Description", None) or "",
        "attributes": attributes, "psets": psets, "qsets": qsets,
        "quantities": [], "materials": materials, "classification": classifications,
        "spatial": spatial, "type": display_value(element_type.Name if element_type else None),
    }
