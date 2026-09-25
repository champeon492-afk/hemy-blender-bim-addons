"""Registration and missing-dependency smoke test in factory Blender."""
import importlib
import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
host = importlib.import_module('bonsai_ux_host')
host.register()
assert bpy.types.BIMUX_PT_context
assert bpy.ops.bimux.diagnostics() == {'FINISHED'}
host.unregister()
assert not hasattr(bpy.types, 'BIMUX_PT_context')
host.register()
assert bpy.types.BIMUX_PT_context
host.unregister()
print('BIM_UX_HOST_SMOKE_OK')
