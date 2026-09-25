"""Bonsai integration. All geometry and distances are in Blender SI metres.

Dimensions are custom IfcAnnotations, without drawing geometry. Only this add-on
displays them. IFC owns persistence; no Blender object pointer is persisted.
"""
import json
import math
import time

import bpy
from mathutils import Vector

from .geometry import PlanLine, measure, motion, chain

PSET = "BPD_DrivingDimension"
TYPE = "BPD_DRIVING_DIMENSION"
_cache = None


def api():
    try:
        import bonsai.tool as tool
        import ifcopenshell.api as ifc_api
        import ifcopenshell.util.element as element
        from bonsai.bim.ifc import IfcStore
    except ImportError as exc:
        raise ValueError("Enable Bonsai before using plan dimensions") from exc
    if not hasattr(bpy.types.Scene, "BIMProperties"):
        raise ValueError("Enable Bonsai before using plan dimensions")
    f = tool.Ifc.get()
    if not f:
        raise ValueError("Open an IFC project in Bonsai first")
    return tool, f, ifc_api, element, IfcStore


def invalidate(*_):
    global _cache
    _cache = None
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def by_guid(f, guid):
    try:
        return f.by_guid(guid)
    except (RuntimeError, ValueError):
        raise ValueError("A referenced IFC element was deleted; remove this dimension") from None


def dimensions():
    global _cache
    _, f, _, util, _ = api()
    now = time.monotonic()
    if _cache and _cache[0] is f and now - _cache[1] < 0.3:
        return _cache[2]
    result = []
    for annotation in f.by_type("IfcAnnotation"):
        if annotation.ObjectType != TYPE:
            continue
        data = util.get_pset(annotation, PSET, "Definition")
        try:
            spec = json.loads(data or "null")
            if not isinstance(spec, dict) or spec.get("version") != 1:
                continue
            if not all(k in spec for k in ("storey", "a", "b", "mode", "offset", "moving")):
                continue
            result.append((annotation.GlobalId, annotation.Name or "Dimension", spec))
        except (ValueError, TypeError):
            continue
    _cache = (f, now, result)
    return result


def get_dimension(guid):
    for entry in dimensions():
        if entry[0] == guid:
            return entry
    raise ValueError("Select a saved dimension first")


def resolve(ref):
    tool, f, _, _, _ = api()
    product = by_guid(f, ref["guid"])
    if ref["kind"] == "GRID":
        matches = [a for a in getattr(product, ref["family"], ()) or () if a.AxisTag == ref["tag"]]
        if len(matches) != 1:
            raise ValueError("Grid axis tag changed or is ambiguous; recreate the dimension")
        product = matches[0]
    obj = tool.Ifc.get_object(product)
    if not obj:
        raise ValueError("A referenced element is not loaded in the viewport")
    return product, obj


def reference(obj):
    tool, _, _, _, _ = api()
    e = tool.Ifc.get_entity(obj)
    if e and e.is_a("IfcWall"):
        return {"kind": "WALL", "guid": e.GlobalId}
    if e and e.is_a("IfcGridAxis"):
        parents = list(e.PartOfU) + list(e.PartOfV) + list(e.PartOfW)
        if len(parents) != 1 or not e.AxisTag:
            raise ValueError("A grid axis must belong to one grid and have a unique axis tag")
        parent = parents[0]
        family = next(k for k in ("UAxes", "VAxes", "WAxes") if e in (getattr(parent, k) or ()))
        if sum(a.AxisTag == e.AxisTag for a in getattr(parent, family)) != 1:
            raise ValueError("Give each axis in this grid family a unique tag")
        return {"kind": "GRID", "guid": parent.GlobalId, "family": family, "tag": e.AxisTag}
    raise ValueError("Select IFC walls or individual IFC grid axes")


def containing_storey(entity):
    _, _, _, util, _ = api()
    container = util.get_container(entity, ifc_class="IfcBuildingStorey")
    seen = set()
    while container and container.id() not in seen:
        if container.is_a("IfcBuildingStorey"):
            return container
        seen.add(container.id())
        container = util.get_aggregate(container)
    return None


def validate_storey(ref, storey):
    _, f, _, _, _ = api()
    e = by_guid(f, ref["guid"])
    s = containing_storey(e)
    if ref["kind"] == "WALL" and (not s or s.GlobalId != storey):
        raise ValueError("Both walls must belong to the selected building storey")
    if ref["kind"] == "GRID" and s and s.GlobalId != storey:
        raise ValueError("This grid belongs to another building storey")


def rigid(obj):
    m = obj.matrix_world.to_3x3()
    if any(abs(m.col[i].length - 1.0) > 1e-5 for i in range(3)) or abs(m.determinant() - 1.0) > 1e-5:
        raise ValueError("Apply IFC geometry edits first; scaled or mirrored objects are unsupported")
    if any(abs(m.col[i].dot(m.col[j])) > 1e-5 for i in range(3) for j in range(i)):
        raise ValueError("Sheared objects are unsupported")


def _straight_wall_axis(entity):
    """Read an unambiguous, straight IFC axis in element-local coordinates.

    Only direction is used, so project length units cancel during
    normalization. Body geometry supplies the physical centre and thickness;
    this avoids confusing a material-layer offset with the body centre.
    """
    representation = getattr(entity, "Representation", None)
    axes = [r for r in getattr(representation, "Representations", ())
            if r.RepresentationIdentifier == "Axis"]
    if len(axes) != 1 or len(axes[0].Items) != 1:
        raise ValueError("Wall requires one explicit straight IFC Axis representation")
    curve = axes[0].Items[0]
    if curve.is_a("IfcPolyline"):
        coordinates = [tuple(point.Coordinates) for point in curve.Points]
    elif curve.is_a("IfcIndexedPolyCurve"):
        coordinates = [tuple(point) for point in curve.Points.CoordList]
        segments = curve.Segments or ()
        if segments and (len(segments) != 1 or not segments[0].is_a("IfcLineIndex")
                         or tuple(segments[0][0]) not in ((1, 2), (2, 1))):
            raise ValueError("Wall Axis must be a straight two-point curve without arcs")
    else:
        raise ValueError("Wall Axis must be a straight polyline; mapped and curved axes are unsupported")
    if len(coordinates) != 2 or any(len(point) not in (2, 3) for point in coordinates):
        raise ValueError("Wall Axis must contain exactly two points")
    if any(not math.isfinite(value) for point in coordinates for value in point):
        raise ValueError("Wall Axis coordinates must be finite")
    start, end = [point if len(point) == 3 else (*point, 0.0) for point in coordinates]
    dx, dy, dz = (end[i] - start[i] for i in range(3))
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length <= 0 or abs(dz) > length * 1e-7:
        raise ValueError("Wall Axis must be non-degenerate and horizontal")
    return dx / length, dy / length


def plan_line(ref):
    tool, _, _, _, _ = api()
    entity, obj = resolve(ref)
    rigid(obj)
    if obj.mode != "OBJECT" or tool.Ifc.is_edited(obj):
        raise ValueError("Finish and save IFC geometry edits before dimensioning")
    if obj.type != "MESH" or not obj.data.vertices:
        raise ValueError("A loaded mesh representation is required")
    coords = [v.co for v in obj.data.vertices]
    if ref["kind"] == "GRID":
        if len(coords) != 2 or not entity.AxisCurve.is_a("IfcPolyline") or len(entity.AxisCurve.Points) != 2:
            raise ValueError("Only straight two-point IFC grid axes are supported")
        return PlanLine(tuple(obj.matrix_world @ coords[0]), tuple(obj.matrix_world @ coords[1]))
    # The IFC Axis, rather than the object-local X direction, determines the
    # wall's longitudinal direction (including walls imported along local Y).
    tx, ty = _straight_wall_axis(entity)
    nx, ny = -ty, tx
    projected = [(v.x * tx + v.y * ty, v.x * nx + v.y * ny) for v in coords]
    x0, x1 = min(p[0] for p in projected), max(p[0] for p in projected)
    y0, y1 = min(p[1] for p in projected), max(p[1] for p in projected)
    if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
        raise ValueError("Wall must have a solid body representation")
    if abs((obj.matrix_world.to_3x3() @ Vector((0, 0, 1))).z - 1) > 1e-5:
        raise ValueError("Only vertical, straight walls are supported")
    # This deliberately rejects curved/tapered walls and mitred joins. A false
    # clear-face measurement is worse than an explicit unsupported case.
    if any(min(abs(p[1] - y0), abs(p[1] - y1)) > 1e-5 for p in projected):
        raise ValueError("Wall has curved, tapered or joined faces; use a straight unjoined wall")
    sides = [[p[0] for p in projected if abs(p[1] - bound) <= 1e-5] for bound in (y0, y1)]
    if any(not side for side in sides) or any(
            abs(min(side) - x0) > 1e-5 or abs(max(side) - x1) > 1e-5 for side in sides):
        raise ValueError("Wall has mitred or nonrectangular ends; use a straight unjoined wall")
    for rel in getattr(entity, "ConnectedTo", ()) + getattr(entity, "ConnectedFrom", ()):
        if rel.is_a("IfcRelConnectsPathElements"):
            raise ValueError("Disconnect wall path joins before using a driving dimension")
    mid = (y0 + y1) / 2
    start = Vector((x0 * tx + mid * nx, x0 * ty + mid * ny, 0))
    end = Vector((x1 * tx + mid * nx, x1 * ty + mid * ny, 0))
    return PlanLine(tuple(obj.matrix_world @ start), tuple(obj.matrix_world @ end), (y1 - y0) / 2)


def storey_z(guid):
    tool, f, _, _, _ = api()
    storey = by_guid(f, guid)
    if not storey.is_a("IfcBuildingStorey"):
        raise ValueError("Select a building storey")
    obj = tool.Ifc.get_object(storey)
    if obj:
        return obj.matrix_world.translation.z
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    return float(ifcopenshell.util.placement.get_local_placement(storey.ObjectPlacement)[2, 3]) * ifcopenshell.util.unit.calculate_unit_scale(f)


def measurement(spec):
    validate_storey(spec["a"], spec["storey"])
    validate_storey(spec["b"], spec["storey"])
    return measure(plan_line(spec["a"]), plan_line(spec["b"]), mode=spec["mode"],
                   offset=spec["offset"], z=storey_z(spec["storey"]))


def format_length(value, context):
    units = context.scene.unit_settings
    # IFC/Bonsai coordinates are metres regardless of the Blender scale_length.
    system = units.system if units.system != "NONE" else "METRIC"
    return bpy.utils.units.to_string(system, "LENGTH", value, precision=4, split_unit=False)


def plan_camera(context):
    space = context.space_data
    return space.camera if getattr(space, "use_local_camera", False) else context.scene.camera


def visible_measurements(context):
    props = context.scene.BPDProperties
    tool, _, _, _, _ = api()
    camera = plan_camera(context)
    cam_entity = tool.Ifc.get_entity(camera) if camera else None
    camera_guid = getattr(cam_entity, "GlobalId", "")
    in_camera = context.region_data and context.region_data.view_perspective == "CAMERA"
    for guid, name, spec in dimensions():
        if spec["storey"] != props.storey_guid:
            continue
        # Unbound dimensions are top-view only; bound dimensions are also
        # visible in their exact plan drawing camera, never another drawing.
        if in_camera and (not spec.get("camera") or spec["camera"] != camera_guid):
            continue
        try:
            if not all(resolve(spec[k])[1].visible_get(view_layer=context.view_layer) for k in ("a", "b")):
                continue
            m = measurement(spec)
            yield guid, format_length(m.value, context), m, guid == props.active_dimension
        except (ValueError, ReferenceError, KeyError):
            continue


def selected_specs(context, make_chain=False):
    from .overlay import is_plan
    if not is_plan(context):
        raise ValueError("Switch to Top Orthographic or a downward orthographic plan camera")
    props = context.scene.BPDProperties
    tool, f, _, _, _ = api()
    storey = by_guid(f, props.storey_guid)
    if not storey.is_a("IfcBuildingStorey"):
        raise ValueError("Choose a building storey")
    objects = list(context.selected_objects)
    if len(objects) < 2 or (not make_chain and len(objects) != 2):
        raise ValueError("Select two elements, or multiple parallel grid axes for a chain")
    if any(not o.visible_get(view_layer=context.view_layer) for o in objects):
        raise ValueError("Only visible elements can be dimensioned")
    active = context.view_layer.objects.active
    if not make_chain:
        if active not in objects:
            raise ValueError("The last selected element must be active")
        objects = [o for o in objects if o != active] + [active]
    refs = [reference(o) for o in objects]
    if len({r["kind"] for r in refs}) != 1:
        raise ValueError("Choose two walls or two grid axes of the same kind")
    if make_chain and any(r["kind"] != "GRID" for r in refs):
        raise ValueError("Automatic chains support parallel grid axes")
    for ref in refs:
        validate_storey(ref, props.storey_guid)
    order = chain([plan_line(r) for r in refs]) if make_chain else list(range(2))
    camera_guid = ""
    region_data = context.region_data or context.space_data.region_3d
    if region_data.view_perspective == "CAMERA":
        cam = tool.Ifc.get_entity(plan_camera(context))
        if not cam or not cam.is_a("IfcAnnotation") or cam.ObjectType != "DRAWING":
            raise ValueError("Use a Bonsai plan drawing camera or Top Orthographic")
        camera_guid = cam.GlobalId
    specs = []
    for i, j in zip(order, order[1:]):
        spec = {"version": 1, "storey": props.storey_guid, "a": refs[i], "b": refs[j],
                "mode": "AXIS" if refs[i]["kind"] == "GRID" else props.wall_mode,
                "moving": "B", "offset": props.offset * (context.scene.unit_settings.scale_length or 1), "camera": camera_guid}
        measurement(spec)
        specs.append(spec)
    return specs


def save_spec(spec, guid=None, name=None):
    _, f, run, util, _ = api()
    if guid:
        annotation = by_guid(f, guid)
    else:
        a, ao = resolve(spec["a"])
        b, bo = resolve(spec["b"])
        name = name or f"{getattr(a, 'AxisTag', None) or a.Name or 'A'} / {getattr(b, 'AxisTag', None) or b.Name or 'B'}"
        annotation = run.run("root.create_entity", f, ifc_class="IfcAnnotation", name=name)
        annotation.ObjectType = TYPE
        run.run("spatial.assign_container", f, products=[annotation], relating_structure=by_guid(f, spec["storey"]))
    existing = util.get_pset(annotation, PSET)
    pset = f.by_id(existing["id"]) if existing else run.run("pset.add_pset", f, product=annotation, name=PSET)
    run.run("pset.edit_pset", f, pset=pset, properties={"Definition": f.create_entity("IfcText", json.dumps(spec, separators=(",", ":")))})
    invalidate()
    return annotation.GlobalId


def prepare_move(spec, target, moving):
    tool, _, _, _, _ = api()
    m = measurement(spec)
    delta = Vector(motion(m, target, moving))
    ref = spec["a" if moving == "A" else "b"]
    e, obj = resolve(ref)
    if obj.library or any(obj.lock_location) or obj.constraints or obj.animation_data:
        raise ValueError("Unlock the moving element and remove animation/constraints first")
    if ref["kind"] == "GRID" and getattr(e, "HasIntersections", ()):
        raise ValueError("This axis controls IFC grid placements; those dependent placements are not supported")
    if ref["kind"] == "WALL" and e.ObjectPlacement and not e.ObjectPlacement.is_a("IfcLocalPlacement"):
        raise ValueError("Only walls with IFC local placements can be moved")
    fixed, _ = resolve(spec["b" if moving == "A" else "a"])
    if len(affected_objects(spec, moving)) > 1 and tool.Ifc.is_moved(obj):
        raise ValueError("Save pending wall placement changes before moving its dependent elements")
    if any(product == fixed for product, _ in affected_objects(spec, moving)):
        raise ValueError("The fixed element is parented to the moving element in IFC")
    for product, child_obj in affected_objects(spec, moving):
        if child_obj.constraints or child_obj.animation_data or child_obj.library or any(child_obj.lock_location):
            raise ValueError("A dependent element is locked, animated or constrained")
        if product != e and tool.Ifc.is_moved(child_obj):
            raise ValueError("Save pending IFC placement edits on dependent elements first")
    return ref, delta


def affected_objects(spec, moving):
    """Loaded objects whose matrices a translation can modify, root first."""
    tool, _, _, _, _ = api()
    entity, obj = resolve(spec["a" if moving == "A" else "b"])
    result = [(entity, obj)]
    visited = set()
    def visit(placement):
        if placement.id() in visited:
            return
        visited.add(placement.id())
        for child in getattr(placement, "ReferencedByPlacements", ()):
            for product in child.PlacesObject:
                child_obj = tool.Ifc.get_object(product)
                if child_obj:
                    result.append((product, child_obj))
            visit(child)
    if getattr(entity, "ObjectPlacement", None):
        visit(entity.ObjectPlacement)
    return result


def move(spec, target, moving):
    tool, f, run, _, _ = api()
    ref, delta = prepare_move(spec, target, moving)
    entity, obj = resolve(ref)
    if delta.length < 1e-9:
        return
    if ref["kind"] == "GRID":
        obj.matrix_world.translation += delta
        tool.Model.create_axis_curve(obj, entity)
        tool.Geometry.record_object_position(obj)
        tool.Ifc.finish_edit(obj)
    else:
        # Collect placements before editing: the API recursively transforms
        # placements; loaded Blender children must receive the same movement.
        descendants = [(p, o, o.matrix_world.copy()) for p, o in affected_objects(spec, moving)[1:]]
        obj.matrix_world.translation += delta
        tool.Geometry.get_blender_offset_type(obj)
        run.run("geometry.edit_object_placement", f, product=entity,
                matrix=tool.Surveyor.get_absolute_matrix(obj), is_si=True, should_transform_children=True)
        for product, child_obj, old in descendants:
            old.translation += delta
            child_obj.matrix_world = old
            tool.Geometry.record_object_position(child_obj)
            tool.Geometry.clear_cache(product)
        tool.Geometry.record_object_position(obj)
        tool.Geometry.clear_cache(entity)
    bpy.context.view_layer.update()
    invalidate()


def delete(guid):
    _, f, run, _, _ = api()
    run.run("root.remove_product", f, product=by_guid(f, guid))
    invalidate()
