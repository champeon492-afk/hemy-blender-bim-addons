"""Build a clean, model-free Blender startup with three BIM workspaces."""
import bpy
from pathlib import Path
from mathutils import Quaternion

out = Path(__file__).resolve().parents[1] / 'qa' / 'bim_ux_startup.blend'
window = bpy.context.window
source = bpy.data.workspaces['Layout']

for name in ('BIM Model', 'BIM Plan', 'BIM Review'):
    window.workspace = source
    before = set(bpy.data.workspaces)
    result = bpy.ops.workspace.duplicate()
    assert result == {'FINISHED'}, (name, result)
    created = set(bpy.data.workspaces) - before
    assert len(created) == 1, (name, created)
    workspace = created.pop()
    workspace.name = name
    window.workspace = workspace
    screen = workspace.screens[0]
    views = [area for area in screen.areas if area.type == 'VIEW_3D']
    assert views, name
    area = max(views, key=lambda a: a.width * a.height)
    space = area.spaces.active
    space.show_region_ui = True
    space.show_region_toolbar = True
    space.overlay.show_overlays = True
    region = space.region_3d
    if name == 'BIM Plan':
        region.view_perspective = 'ORTHO'
        region.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
    elif name == 'BIM Review':
        region.view_perspective = 'PERSP'
    else:
        region.view_perspective = 'PERSP'
    for prop_area in (a for a in screen.areas if a.type == 'PROPERTIES'):
        prop_area.spaces.active.context = 'OBJECT'

window.workspace = bpy.data.workspaces['BIM Model']
assert not bpy.data.filepath
assert not any(obj.name.startswith('Ifc') for obj in bpy.data.objects)
bpy.ops.wm.save_as_mainfile(filepath=str(out), check_existing=False)
print('BIM_UX_STARTUP', out, [w.name for w in bpy.data.workspaces if w.name.startswith('BIM ')])
