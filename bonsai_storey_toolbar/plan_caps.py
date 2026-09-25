# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-destructive cut-face display for solid meshes in a vertical view range.

Native viewport clipping discards surfaces without capping them. In a top
orthographic view a cut vertical wall is consequently invisible. Intersect the
evaluated mesh with the two limits and draw the filled sections as an overlay.
Polygon parity and constrained triangulation retain holes and disconnected parts.
"""
import sys
import bpy
from bpy.app.handlers import persistent
from mathutils import Vector

_cache = {}
_handler = None
_generation = 0


def tolerance(lower, upper):
    return min(1e-5, (upper-lower)*1e-4)


def mesh_section(vertices, triangles, z):
    """Return triangulated material area and boundary segments at a world Z."""
    from shapely.geometry import LineString
    from shapely.ops import polygonize
    from shapely import constrained_delaunay_triangles
    segments = set()
    for triangle in triangles:
        pts = [vertices[i] for i in triangle]
        distances = [p[2]-z for p in pts]
        if min(distances) > 1e-9 or max(distances) < -1e-9:
            continue
        if all(abs(d) < 1e-9 for d in distances):
            continue
        crossings = set()
        for i in range(3):
            p, q = pts[i], pts[(i+1)%3]
            a, b = p[2]-z, q[2]-z
            if abs(a) < 1e-9:
                crossings.add((round(p[0], 6), round(p[1], 6)))
            if a*b < 0:
                t = a/(a-b)
                crossings.add((round(p[0]+t*(q[0]-p[0]), 6),
                               round(p[1]+t*(q[1]-p[1]), 6)))
        if len(crossings) == 2:
            a, b = sorted(crossings)
            if a != b:
                segments.add((a,b))
    if not segments:
        return [], [], 0.0
    def inside(x, y):
        parity = False
        for (ax,ay), (bx,by) in segments:
            if (ay>y) != (by>y) and x < ax+(y-ay)*(bx-ax)/(by-ay):
                parity = not parity
        return parity
    result, lines, area = [], [], 0.0
    for polygon in polygonize([LineString(edge) for edge in segments]):
        point = polygon.representative_point()
        if not inside(point.x, point.y):
            continue
        area += polygon.area
        for triangle in constrained_delaunay_triangles(polygon).geoms:
            result.extend((x,y,z) for x,y in list(triangle.exterior.coords)[:3])
        for ring in (polygon.exterior, *polygon.interiors):
            coords = list(ring.coords)
            for a,b in zip(coords,coords[1:]):
                lines.extend(((a[0],a[1],z), (b[0],b[1],z)))
    return result, lines, area


def prepare(window, scene, lower, upper):
    """Cache evaluated sections per view layer; no objects or meshes are added."""
    key = (scene.as_pointer(), window.view_layer.as_pointer())
    layer = window.view_layer
    objects = [o for o in layer.objects if o.type == 'MESH' and o.visible_get(view_layer=layer)]
    signature = (lower, upper, _generation, tuple(o.as_pointer() for o in objects))
    existing = _cache.get(key)
    if existing and existing['signature'] == signature:
        return existing
    epsilon = tolerance(lower, upper)
    # Keep intersections away from nearly coincident mesh vertices: snapping
    # an extremely short triangle segment can otherwise open the section loop.
    inset = min(0.001, (upper-lower)*0.01)
    cuts = (lower+max(epsilon*2, inset), upper-max(epsilon*2, inset))
    depsgraph = window.view_layer.depsgraph
    records, intersecting = [], []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        corners = [evaluated.matrix_world @ Vector(p) for p in evaluated.bound_box]
        low, high = min(p.z for p in corners), max(p.z for p in corners)
        if high > lower+epsilon and low < upper-epsilon:
            intersecting.append(obj.name)
        relevant = [z for z in cuts if low < z < high]
        if not relevant:
            continue
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            vertices = [tuple(evaluated.matrix_world @ v.co) for v in mesh.vertices]
            triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
            positions, boundaries, areas = [], [], []
            for z in relevant:
                cap, lines, area = mesh_section(vertices, triangles, z)
                positions.extend(cap)
                boundaries.extend(lines)
                areas.append((z,area))
            if positions:
                records.append(dict(object=obj, triangles=positions, lines=boundaries,
                                    sections=areas, batches=None))
        finally:
            evaluated.to_mesh_clear()
    result = dict(signature=signature, records=records, lower=lower, upper=upper,
                  intersecting_objects=intersecting)
    _cache[key] = result
    return result


def clear(scene=None):
    if scene is None:
        _cache.clear()
    else:
        for key in list(_cache):
            if key[0] == scene.as_pointer():
                _cache.pop(key, None)


@persistent
def invalidate(scene, depsgraph):
    global _generation
    if any(update.is_updated_geometry or update.is_updated_transform for update in depsgraph.updates):
        _generation += 1


def draw():
    context = bpy.context
    if not context.region_data or context.space_data.type != 'VIEW_3D':
        return
    root = sys.modules.get(__package__)
    ranges = root.view_range
    if ranges._saving or context.region_data.as_pointer() not in ranges._sessions:
        return
    cached = _cache.get((context.scene.as_pointer(), context.view_layer.as_pointer()))
    if not cached:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    depth, mask, blend = gpu.state.depth_test_get(), gpu.state.depth_mask_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set('NONE')
        for record in cached['records']:
            obj = record['object']
            if not obj.visible_get(view_layer=context.view_layer):
                continue
            if record['batches'] is None:
                record['batches'] = (
                    batch_for_shader(shader, 'TRIS', {'pos':record['triangles']}),
                    batch_for_shader(shader, 'LINES', {'pos':record['lines']}))
            face, border = record['batches']
            shader.bind()
            if context.space_data.shading.type != 'WIREFRAME':
                shader.uniform_float('color', (0.65,0.67,0.70,1))
                face.draw(shader)
            shader.uniform_float('color', (1.0,0.55,0.12,1) if obj.select_get() else (0.16,0.18,0.20,1))
            border.draw(shader)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(mask)
        gpu.state.blend_set(blend)


def register():
    global _handler
    if invalidate not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(invalidate)
    if not bpy.app.background:
        _handler = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _handler
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    if invalidate in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(invalidate)
    clear()
