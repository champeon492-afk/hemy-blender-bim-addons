# SPDX-License-Identifier: GPL-3.0-or-later
"""World-elevation viewport clipping; never edits geometry or hide flags."""
import math
import sys
import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty
from . import plan_caps

_sessions = {}
_updating = False
_saving = False
_hook = None


def addon():
    return sys.modules[__package__]


def building_id(entity):
    visited = set()
    while entity and entity.id() not in visited:
        visited.add(entity.id())
        if entity.is_a('IfcBuilding'):
            return entity.id()
        parents = getattr(entity, 'Decomposes', ())
        entity = parents[0].RelatingObject if parents else None
    return None


def level_height(entity):
    return float(addon().storey_frame(entity).translation.z)


def levels_for(scene):
    root = addon()
    selected = root.selected_storey(scene)
    file = root.ifc_file()
    if not selected or not file:
        return []
    building = building_id(selected)
    result = []
    for entity in file.by_type('IfcBuildingStorey'):
        if building_id(entity) == building and root.api().Ifc.get_object(entity):
            result.append(entity)
    return sorted(result, key=lambda e: (level_height(e), (e.Name or '').casefold(), e.id()))


def resolve_level(scene, guid):
    if not guid:
        entity = addon().selected_storey(scene)
    else:
        try:
            entity = addon().ifc_file().by_guid(guid)
        except (AttributeError, RuntimeError, ValueError):
            entity = None
    if not entity or not entity.is_a('IfcBuildingStorey'):
        raise ValueError('Choose an available IFC storey for the view range')
    return entity


def next_level(scene):
    selected = resolve_level(scene, '')
    height = level_height(selected)
    return next((e for e in levels_for(scene) if level_height(e) > height + 1e-5), None)


def level_bounds(scene):
    settings = scene.ifc_storey_toolbar
    lower = level_height(resolve_level(scene, settings.range_lower_guid))
    if settings.range_upper_guid:
        upper = level_height(resolve_level(scene, settings.range_upper_guid))
    else:
        above = next_level(scene)
        upper = level_height(above) if above else level_height(resolve_level(scene, '')) + settings.range_fallback_height
    return lower + settings.range_lower_offset, upper + settings.range_upper_offset


def range_bounds(scene):
    settings = scene.ifc_storey_toolbar
    # Require a valid model/storey even for custom elevations.
    resolve_level(scene, '')
    low, high = ((settings.range_min, settings.range_max)
                 if settings.range_mode == 'CUSTOM' else level_bounds(scene))
    if not all(math.isfinite(v) for v in (low, high)):
        raise ValueError('Enter finite lower and upper elevations')
    if high - low < 0.0001:
        raise ValueError('Upper limit must be above the lower limit')
    return low, high


def make_range_planes(lower, upper):
    if not math.isfinite(lower) or not math.isfinite(upper) or upper <= lower:
        raise ValueError('Upper limit must be above the lower limit')
    # Blender has six slots. Repeating planes leaves X and Y unrestricted.
    # Ignore zero-thickness contact with floors immediately outside the range.
    epsilon = plan_caps.tolerance(lower, upper)
    return ((0.0, 0.0, 1.0, -(lower+epsilon)),
            (0.0, 0.0, -1.0, upper-epsilon)) * 3


def same_planes(a, b):
    return all(abs(x-y) <= max(1e-5, abs(y)*1e-7)
               for p, q in zip(a, b) for x, y in zip(p, q))


def section_session(scene):
    module = sys.modules.get('bonsai_section_box')
    session = getattr(module, '_session', None)
    return session if session and session.get('scene') == scene else None


def has_bonsai_planes(scene):
    props = getattr(scene, 'BIMProjectProperties', None)
    return bool(props and any(item.obj for item in props.clipping_planes))


def viewport_contexts(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            if space.region_quadviews:
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region:
                yield window, area, region, space, space.region_3d


def initialize_clip(session):
    window, area, region = session['window'], session['area'], session['region']
    session['space'].shading.type = 'SOLID' if session['space'].shading.type not in {'SOLID', 'WIREFRAME'} else session['space'].shading.type
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.clip_border('EXEC_DEFAULT', xmin=0, ymin=0,
                                  xmax=max(region.width, 1), ymax=max(region.height, 1))


def restore_session(session, restore=True):
    try:
        rv, space, old = session['rv'], session['space'], session['old']
        owned = same_planes(rv.clip_planes, session['last'])
        # A section box created during range clipping saved our planes as its
        # baseline. Give it the original baseline before handing over control.
        section = section_session(session['scene'])
        if section and section.get('rv3d') == rv:
            previous = section['previous']
            if same_planes(previous['clip_planes'], session['last']):
                previous.update(use_clip_planes=old['enabled'], clip_planes=old['planes'],
                                use_box_clip=old['box'], shading=old['shading'])
            return
        if not restore or (rv.use_clip_planes and not owned):
            return
        if old['enabled']:
            if not rv.use_clip_planes:
                initialize_clip(session)
            rv.clip_planes = old['planes']
            rv.use_clip_planes = True
        else:
            if rv.use_clip_planes and not bpy.app.background:
                if space.shading.type not in {'SOLID', 'WIREFRAME'}:
                    space.shading.type = 'SOLID'
                with bpy.context.temp_override(window=session['window'], area=session['area'], region=session['region']):
                    bpy.ops.view3d.clip_border('INVOKE_DEFAULT')
            rv.use_clip_planes = False
            rv.clip_planes = old['planes']
        rv.use_box_clip = old['box']
        space.shading.type = old['shading']
        session['area'].tag_redraw()
    except (ReferenceError, RuntimeError, TypeError):
        pass


def release_range(scene=None):
    plan_caps.clear(scene)
    for key, session in list(_sessions.items()):
        if scene is not None and session['scene'] != scene:
            continue
        restore_session(session)
        _sessions.pop(key, None)


def set_status(settings, text):
    if settings.range_status != text:
        settings.range_status = text


def apply_range(scene):
    global _updating
    if _saving:
        return False
    settings = scene.ifc_storey_toolbar
    if not settings.range_enabled:
        release_range(scene)
        set_status(settings, '')
        return False
    reason = ''
    if section_session(scene):
        reason = 'Paused while Section Box is active'
    elif has_bonsai_planes(scene):
        reason = 'Paused while Bonsai clipping planes are active'
    elif any(w.scene == scene and w.view_layer.objects.active and
             w.view_layer.objects.active.mode != 'OBJECT'
             for w in bpy.context.window_manager.windows):
        reason = 'Paused in Edit Mode; return to Object Mode'
    try:
        lower, upper = range_bounds(scene)
    except (ValueError, RuntimeError) as error:
        reason = str(error)
    if reason:
        release_range(scene)
        set_status(settings, reason)
        return False
    planes = make_range_planes(lower, upper)
    _install_hook()
    active_keys = set()
    for window, area, region, space, rv in viewport_contexts(scene):
        key = rv.as_pointer()
        active_keys.add(key)
        session = _sessions.get(key)
        if session and session['scene'] != scene:
            restore_session(session)
            _sessions.pop(key)
            session = None
        if session and (not rv.use_clip_planes or not same_planes(rv.clip_planes, session['last'])):
            # Alt+B or another viewport tool took ownership. Preserve its new
            # clipping and turn this range off instead of fighting the user.
            _updating = True
            settings.range_enabled = False
            _updating = False
            _sessions.pop(key, None)
            release_range(scene)
            set_status(settings, '')
            return False
        if not session:
            session = dict(scene=scene, window=window, area=area, region=region,
                           space=space, rv=rv, last=planes,
                           old=dict(enabled=rv.use_clip_planes,
                                    planes=tuple(tuple(p) for p in rv.clip_planes),
                                    box=rv.use_box_clip, shading=space.shading.type))
            _sessions[key] = session
        if not rv.use_clip_planes:
            initialize_clip(session)
        if space.shading.type not in {'SOLID', 'WIREFRAME'}:
            space.shading.type = 'SOLID'
        rv.use_box_clip = False
        if not same_planes(rv.clip_planes, planes) or not rv.use_clip_planes:
            rv.clip_planes = planes
            rv.use_box_clip = False
            rv.use_clip_planes = True
            area.tag_redraw()
        session['last'] = planes
        caps = plan_caps.prepare(window, scene, lower, upper)
        if session.get('cap_signature') != caps['signature']:
            session['cap_signature'] = caps['signature']
            area.tag_redraw()
    # Restore closed, switched, or inactive workspace viewports too.
    for key, session in list(_sessions.items()):
        if session['scene'] == scene and key not in active_keys:
            restore_session(session)
            _sessions.pop(key, None)
    set_status(settings, '' if active_keys else 'Open a regular 3D viewport to apply the range')
    return bool(active_keys)


def refresh_all():
    for scene in bpy.data.scenes:
        try:
            apply_range(scene)
        except (ReferenceError, RuntimeError, ValueError) as error:
            release_range(scene)
            set_status(scene.ifc_storey_toolbar, str(error))


def settings_changed(self, context):
    if not _updating and context.scene:
        apply_range(context.scene)


def mode_changed(self, context):
    global _updating
    if _updating:
        return
    if self.range_mode == 'CUSTOM':
        try:
            low, high = level_bounds(context.scene)
            _updating = True
            self.range_min, self.range_max = low, high
        except (ValueError, RuntimeError):
            pass
        finally:
            _updating = False
    settings_changed(self, context)


def reset_range(scene):
    global _updating
    settings = scene.ifc_storey_toolbar
    _updating = True
    try:
        settings.range_mode = 'LEVELS'
        settings.range_lower_guid = settings.range_upper_guid = ''
        settings.range_lower_offset = settings.range_upper_offset = 0
        settings.range_fallback_height = 3
        settings.range_enabled = True
    finally:
        _updating = False
    apply_range(scene)


def _install_hook():
    global _hook
    if _hook or not hasattr(bpy.types.Scene, 'BIMProjectProperties'):
        return
    try:
        from bonsai.bim.module.project.operator import RefreshClippingPlanes
    except ImportError:
        return
    original = RefreshClippingPlanes.refresh_clipping_planes
    def refresh(operator, context):
        first = next((a for a in context.screen.areas if a.type == 'VIEW_3D'), None)
        if first and first.spaces.active.region_3d.as_pointer() in _sessions:
            if not has_bonsai_planes(context.scene):
                return {'FINISHED'}
            release_range(context.scene)
        return original(operator, context)
    RefreshClippingPlanes.refresh_clipping_planes = refresh
    _hook = (RefreshClippingPlanes, original, refresh)


@persistent
def load_pre(_):
    release_range()


@persistent
def save_pre(_):
    global _saving
    refresh_all()
    _saving = True
    for session in _sessions.values():
        try:
            rv, old = session['rv'], session['old']
            rv.clip_planes = old['planes']
            rv.use_clip_planes, rv.use_box_clip = old['enabled'], old['box']
        except ReferenceError:
            pass


@persistent
def save_post(_):
    global _saving
    _saving = False
    for session in _sessions.values():
        try:
            # Allocation still exists; don't call clip_border a second time.
            session['rv'].clip_planes = session['last']
            session['rv'].use_clip_planes = True
            session['rv'].use_box_clip = False
        except ReferenceError:
            pass
    refresh_all()


class IFCSTOREY_OT_range_reset(bpy.types.Operator):
    bl_idname = 'ifcstorey.range_reset'
    bl_label = 'Reset to Level-to-Level'
    bl_description = 'Use the selected level to the next higher level, with zero offsets'
    def execute(self, context):
        reset_range(context.scene)
        return {'FINISHED'}


class IFCSTOREY_OT_range_level(bpy.types.Operator):
    bl_idname = 'ifcstorey.range_level'
    bl_label = 'Set View Range Level'
    bound: StringProperty()
    guid: StringProperty()
    def execute(self, context):
        s = context.scene.ifc_storey_toolbar
        if self.bound == 'LOWER':
            s.range_lower_guid = self.guid
        else:
            s.range_upper_guid = self.guid
        return {'FINISHED'}


def draw_level_menu(layout, context, bound):
    op = layout.operator('ifcstorey.range_level', text='Selected Level' if bound == 'LOWER' else 'Next Higher Level', icon='FILE_REFRESH')
    op.bound, op.guid = bound, ''
    layout.separator()
    for entity in levels_for(context.scene):
        op = layout.operator('ifcstorey.range_level', text=entity.Name or 'Unnamed Storey')
        op.bound, op.guid = bound, entity.GlobalId


class IFCSTOREY_MT_range_lower(bpy.types.Menu):
    bl_label = 'Lower Level'
    def draw(self, context):
        draw_level_menu(self.layout, context, 'LOWER')


class IFCSTOREY_MT_range_upper(bpy.types.Menu):
    bl_label = 'Upper Level'
    def draw(self, context):
        draw_level_menu(self.layout, context, 'UPPER')


def level_label(scene, guid, default):
    if not guid:
        return default
    try:
        return resolve_level(scene, guid).Name or 'Unnamed Storey'
    except ValueError:
        return 'Missing Level'


def draw_range(layout, context):
    scene = context.scene
    s = scene.ifc_storey_toolbar
    layout.prop(s, 'range_enabled', text='Enable View Range')
    col = layout.column()
    col.active = s.range_enabled
    col.prop(s, 'range_mode', expand=True)
    if s.range_mode == 'LEVELS':
        col.label(text='Lower limit')
        col.menu('IFCSTOREY_MT_range_lower', text=level_label(scene, s.range_lower_guid, 'Selected Level'))
        col.prop(s, 'range_lower_offset', text='Offset')
        col.separator(factor=0.5)
        col.label(text='Upper limit')
        col.menu('IFCSTOREY_MT_range_upper', text=level_label(scene, s.range_upper_guid, 'Next Higher Level'))
        col.prop(s, 'range_upper_offset', text='Offset')
        try:
            if not s.range_upper_guid and not next_level(scene):
                col.label(text='No higher level', icon='INFO')
                col.prop(s, 'range_fallback_height', text='Height Above Level')
        except ValueError:
            pass
    else:
        col.prop(s, 'range_min', text='Lower Elevation')
        col.prop(s, 'range_max', text='Upper Elevation')
    col.separator(factor=0.5)
    try:
        low, high = range_bounds(scene)
        unit = lambda value: bpy.utils.units.to_string('METRIC', 'LENGTH', value, precision=3)
        col.label(text=f'Visible: {unit(low)} to {unit(high)}')
    except ValueError:
        pass
    if s.range_status:
        box = layout.box()
        # Keep messages within a normal-width sidebar/popover.
        import textwrap
        for line in textwrap.wrap(s.range_status, width=37):
            box.label(text=line)
    col.operator('ifcstorey.range_reset', icon='LOOP_BACK')
    layout.label(text='Solid / Wireframe · Object Mode', icon='INFO')
    layout.label(text='Applies in plan and 3D views')


class IFCSTOREY_PT_view_range(bpy.types.Panel):
    bl_label = 'View Range'
    bl_ui_units_x = 18
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'IFC Storeys'
    def draw(self, context):
        draw_range(self.layout, context)


CLASSES = (IFCSTOREY_OT_range_reset, IFCSTOREY_OT_range_level,
           IFCSTOREY_MT_range_lower, IFCSTOREY_MT_range_upper, IFCSTOREY_PT_view_range)


def register():
    plan_caps.register()
    for collection, callback in ((bpy.app.handlers.load_pre, load_pre),
                                 (bpy.app.handlers.save_pre, save_pre),
                                 (bpy.app.handlers.save_post, save_post),
                                 (getattr(bpy.app.handlers, 'save_post_fail', []), save_post)):
        if callback not in collection:
            collection.append(callback)


def unregister():
    global _hook, _saving
    release_range()
    plan_caps.unregister()
    _saving = False
    if _hook:
        cls, original, wrapper = _hook
        if cls.refresh_clipping_planes is wrapper:
            cls.refresh_clipping_planes = original
        _hook = None
    for collection, callback in ((bpy.app.handlers.load_pre, load_pre),
                                 (bpy.app.handlers.save_pre, save_pre),
                                 (bpy.app.handlers.save_post, save_post),
                                 (getattr(bpy.app.handlers, 'save_post_fail', []), save_post)):
        if callback in collection:
            collection.remove(callback)
