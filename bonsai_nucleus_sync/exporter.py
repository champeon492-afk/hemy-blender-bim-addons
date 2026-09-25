"""Capture Blender on its main thread; transport sees ordinary Python values."""
import uuid
import bpy

SUPPORTED = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'EMPTY'}
OBJECT_KEY = '_bonsai_nucleus_sync_id'


def object_id(obj, used, owners):
    value = obj.get(OBJECT_KEY, '')
    if obj.library:
        value = uuid.uuid5(uuid.NAMESPACE_URL, obj.library.filepath + ':' + obj.name_full).hex
    elif (not isinstance(value, str) or len(value) != 32 or value in used
          or (value in owners and owners[value] != obj.as_pointer())):
        value = uuid.uuid4().hex
        obj[OBJECT_KEY] = value
    try:
        int(value, 16)
    except ValueError:
        value = uuid.uuid4().hex
        obj[OBJECT_KEY] = value
    used.add(value)
    owners[value] = obj.as_pointer()
    return value


def ifc_metadata(obj):
    try:
        import bonsai.tool as tool
        entity = tool.Ifc.get_entity(obj)
    except (ImportError, AttributeError, RuntimeError, ReferenceError):
        entity = None
    if not entity:
        return {}
    return {'GlobalId': str(getattr(entity, 'GlobalId', '') or ''),
            'Class': entity.is_a(), 'StepId': str(entity.id()),
            'Name': str(getattr(entity, 'Name', '') or '')[:4096],
            'Description': str(getattr(entity, 'Description', '') or '')[:4096]}


def material_colors(obj):
    values = []
    for slot in obj.material_slots:
        mat = slot.material
        color = list(mat.diffuse_color) if mat else [0.65, 0.65, 0.65, 1.0]
        if mat and mat.use_nodes and mat.node_tree:
            node = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if node:
                color = list(node.inputs['Base Color'].default_value)
                color[3] = float(node.inputs['Alpha'].default_value)
        values.append([float(c) for c in color])
    return values or [[0.65, 0.65, 0.65, 1.0]]


def mesh_data(obj_eval, depsgraph, colors):
    if obj_eval.type == 'EMPTY':
        return None
    mesh = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    if mesh is None:
        return None
    try:
        counts = [len(p.vertices) for p in mesh.polygons]
        indices = [v for p in mesh.polygons for v in p.vertices]
        face_colors = [colors[min(p.material_index, len(colors) - 1)] for p in mesh.polygons]
        normals = [list(n.vector) for n in mesh.corner_normals]
        uv_layer = mesh.uv_layers.active
        return {'points': [list(v.co) for v in mesh.vertices], 'counts': counts, 'indices': indices,
                'colors': [c[:3] for c in face_colors],
                'opacity': [max(0, min(1, c[3])) for c in face_colors],
                'normals': normals,
                'uv': [list(item.uv) for item in uv_layer.data] if uv_layer else []}
    finally:
        obj_eval.to_mesh_clear()


class Exporter:
    def __init__(self):
        self.revision = 0
        self.geometry = {}
        self.all_geometry = 0
        self.owners = {}

    def dirty(self, depsgraph=None):
        self.revision += 1
        if depsgraph is None:
            self.all_geometry = self.revision
            return
        for update in depsgraph.updates:
            item = update.id
            if isinstance(item, bpy.types.Object) and update.is_updated_geometry:
                self.geometry[item.original.as_pointer()] = self.revision
            elif isinstance(item, (bpy.types.Material, bpy.types.NodeTree)):
                self.all_geometry = self.revision
            elif isinstance(item, (bpy.types.Mesh, bpy.types.Curve, bpy.types.MetaBall)):
                # Linked mesh datablocks can affect several objects.
                for obj in bpy.context.scene.objects:
                    if obj.data and obj.data.original == item.original:
                        self.geometry[obj.as_pointer()] = self.revision

    def capture(self, context, baseline, collection=None):
        previous = baseline or {}
        full = baseline is None
        used, state, updates = set(), {}, []
        # Refresh membership before filtering newly linked/duplicated objects.
        context.view_layer.update()
        candidates = list(collection.all_objects if collection else context.scene.objects)
        candidates = [o for o in candidates if o.type in SUPPORTED and o.name in context.view_layer.objects]
        pointers = {o.as_pointer() for o in candidates}
        # Undo can reconstruct datablocks at new addresses. Keep their saved IDs.
        self.owners = {key: pointer for key, pointer in self.owners.items() if pointer in pointers}
        self.geometry = {pointer: rev for pointer, rev in self.geometry.items() if pointer in pointers}
        for obj in candidates:
            if obj.mode == 'EDIT':
                obj.update_from_editmode()
        depsgraph = context.evaluated_depsgraph_get()
        for obj in candidates:
            oid = object_id(obj, used, self.owners)
            evaluated = obj.evaluated_get(depsgraph)
            colors = material_colors(evaluated)
            record = {'id': oid, 'name': obj.name_full[:4096],
                      'matrix': [float(v) for row in evaluated.matrix_world for v in row],
                      'visible': bool(obj.visible_get(view_layer=context.view_layer) and not obj.hide_render),
                      'ifc': ifc_metadata(obj)}
            geometry_rev = max(self.all_geometry, self.geometry.get(obj.as_pointer(), 0))
            stamp = (geometry_rev, obj.data.as_pointer() if obj.data else 0, colors)
            old = previous.get(oid)
            geometry_changed = full or not old or old[1] != stamp or obj.mode == 'EDIT'
            if geometry_changed or not old or old[0] != record:
                update = dict(record)
                if geometry_changed:
                    update['mesh'] = mesh_data(evaluated, depsgraph, colors)
                updates.append(update)
            state[oid] = (record, stamp)
        return updates, sorted(set(previous) - set(state)), state
