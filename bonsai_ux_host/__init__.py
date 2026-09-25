"""Task-oriented UI over installed Bonsai and IFC companion add-ons.

Only public Blender operators and registered properties are used. Optional add-ons
remain independently installable and this module never edits IFC data on load.
"""

bl_info = {
    "name": "Bonsai UX Host",
    "author": "HEMY AS",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D View > BIM",
    "description": "Task-oriented context, modeling, review and collaboration controls",
    "category": "3D View",
}

import importlib
import bpy
from bpy.props import EnumProperty, StringProperty

_STOREY_ITEMS = []


def available(module):
    return module in bpy.context.preferences.addons


def operator_exists(idname):
    try:
        group, name = idname.split(".", 1)
        return getattr(getattr(bpy.ops, group), name).get_rna_type() is not None
    except (AttributeError, KeyError, RuntimeError, ValueError):
        return False


def action(layout, idname, label, icon='NONE', *, enabled=True, **properties):
    row = layout.row(align=True)
    row.enabled = enabled and operator_exists(idname)
    if not operator_exists(idname):
        row.label(text=f"{label} unavailable", icon='INFO')
        return
    op = row.operator(idname, text=label, icon=icon)
    for key, value in properties.items():
        setattr(op, key, value)


def scene_prop(context, name):
    return getattr(context.scene, name, None) if context.scene else None


def ifc_file():
    tool = ifc_tool()
    try:
        return tool.Ifc.get() if tool else None
    except (AttributeError, RuntimeError):
        return None


def ifc_tool():
    try:
        return importlib.import_module('bonsai.tool')
    except ImportError:
        return None


def ifc_element(context):
    tool = ifc_tool()
    if not tool or not context.active_object:
        return None
    try:
        return tool.Ifc.get_entity(context.active_object)
    except (AttributeError, RuntimeError, ValueError):
        return None


def selected_type(entity):
    if not entity:
        return None
    try:
        from ifcopenshell.util.element import get_type
        return get_type(entity)
    except (ImportError, RuntimeError, ValueError):
        return None


def storey_name(context):
    settings = scene_prop(context, 'ifc_storey_toolbar')
    if not settings or not settings.selected_guid:
        return 'Select Storey'
    tool = ifc_tool()
    try:
        entity = tool.Ifc.get().by_guid(settings.selected_guid)
        return entity.Name or 'Unnamed Storey'
    except (AttributeError, RuntimeError, ValueError):
        return 'Select Storey'


def context_names(context):
    file = ifc_file()
    if not file:
        return ('No IFC project', 'No building')
    try:
        projects = file.by_type('IfcProject')
        project = (projects[0].Name or 'IFC project') if projects else 'IFC project'
        settings = scene_prop(context, 'ifc_storey_toolbar')
        spatial = file.by_guid(settings.selected_guid) if settings and settings.selected_guid else None
        if spatial:
            from ifcopenshell.util.element import get_aggregate
            while spatial and not spatial.is_a('IfcBuilding'):
                spatial = get_aggregate(spatial)
        if not spatial:
            buildings = file.by_type('IfcBuilding')
            spatial = buildings[0] if len(buildings) == 1 else None
        return project, (spatial.Name or 'Unnamed building') if spatial else 'Choose building'
    except (AttributeError, RuntimeError, ValueError):
        return ('IFC project', 'Choose building')


def element_storey_name(entity):
    try:
        from ifcopenshell.util.element import get_container, get_aggregate
        spatial = get_container(entity)
        while spatial and not spatial.is_a('IfcBuildingStorey'):
            spatial = get_aggregate(spatial)
        return (spatial.Name or 'Unnamed storey') if spatial else 'Unassigned'
    except (ImportError, AttributeError, RuntimeError, ValueError):
        return 'Unknown'


def storey_items(_self, context):
    global _STOREY_ITEMS
    file = ifc_file()
    if not file:
        _STOREY_ITEMS = [('NONE', 'No IFC project', '')]
        return _STOREY_ITEMS
    try:
        storeys = file.by_type('IfcBuildingStorey')
        _STOREY_ITEMS = [(s.GlobalId, s.Name or f'Storey #{s.id()}', '') for s in
                         sorted(storeys, key=lambda s: (float(s.Elevation or 0), (s.Name or '').casefold()))]
    except (AttributeError, RuntimeError, ValueError):
        _STOREY_ITEMS = []
    return _STOREY_ITEMS or [('NONE', 'No storeys', '')]


def workplane_name(context):
    settings = scene_prop(context, 'workplane_toolkit')
    if settings and settings.active:
        return settings.source_label or 'Active'
    return 'World XY / Storey'


def nucleus_state(context):
    settings = scene_prop(context, 'bonsai_nucleus_sync')
    if settings:
        status = (settings.status or '').lower()
        for term, label, icon in (
            ('conflict', 'Conflict', 'ERROR'), ('error', 'Error', 'ERROR'),
            ('read-only', 'Read-only', 'LOCKED'), ('connect', 'Connecting', 'TIME'),
            ('pause', 'Paused', 'PAUSE'), ('live', 'Live', 'LINKED'),
            ('syncing', 'Live', 'LINKED'),
        ):
            if term in status:
                return label, icon
    return 'Offline', 'UNLINKED'


def nucleus_browser_state(context):
    manager = getattr(context, 'window_manager', None)
    return getattr(manager, 'nucleus_files', None) if manager else None


class BIMUX_OT_nucleus_browser(bpy.types.Operator):
    bl_idname = 'bimux.nucleus_browser'
    bl_label = 'Nucleus Project Browser'
    bl_description = 'Open the native Nucleus browser; no connection or transfer starts automatically'
    kind: EnumProperty(name='Project format', items=[('IFC', 'IFC', 'Native IFC project'),
                                                     ('BLEND', 'Blend', 'Blender project')])

    @classmethod
    def poll(cls, context):
        return nucleus_browser_state(context) is not None

    def invoke(self, context, _event):
        state = nucleus_browser_state(context)
        if state is None:
            return {'CANCELLED'}
        state.kind = self.kind
        return context.window_manager.invoke_popup(self, width=430)

    def draw(self, context):
        try:
            module = importlib.import_module('bonsai_nucleus_files.ui')
            module.NUCLEUS_PT_browser.draw(self, context)
        except (ImportError, AttributeError, RuntimeError) as error:
            self.layout.label(text=f'Nucleus browser unavailable: {error}', icon='INFO')

    def execute(self, _context):
        return {'FINISHED'}


class BIMUX_MT_nucleus_files(bpy.types.Menu):
    bl_idname = 'BIMUX_MT_nucleus_files'
    bl_label = 'BIM Projects on Nucleus'

    def draw(self, _context):
        layout = self.layout
        for label, kind, icon in (
            ('Open IFC from Nucleus...', 'IFC', 'FILE_3D'),
            ('Open Blend from Nucleus...', 'BLEND', 'FILE_BLEND'),
            ('Save IFC to Nucleus...', 'IFC', 'FILE_TICK'),
            ('Save Blend to Nucleus...', 'BLEND', 'FILE_TICK'),
        ):
            layout.operator('bimux.nucleus_browser', text=label, icon=icon).kind = kind
        layout.separator()
        layout.label(text='Choose a file or Save in the browser.')


def draw_file_menu(self, _context):
    self.layout.menu('BIMUX_MT_nucleus_files', icon='NETWORK_DRIVE')


class BIMUX_PT_nucleus_status(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_nucleus_status'
    bl_label = 'Nucleus Status'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'

    def draw(self, context):
        layout = self.layout
        status, icon = nucleus_state(context)
        layout.label(text='Live Sync: ' + status, icon=icon)
        settings = scene_prop(context, 'bonsai_nucleus_sync')
        if settings:
            layout.label(text=(settings.status or 'Stopped')[:48])
            if status != 'Offline':
                action(layout, 'bonsai_nucleus.stop', 'Leave Live Sync', 'CANCEL')
        else:
            layout.label(text='Live Sync add-on unavailable', icon='INFO')
        layout.separator()
        for kind, label in (('IFC', 'Browse IFC projects'), ('BLEND', 'Browse Blend projects')):
            row = layout.row()
            row.enabled = nucleus_browser_state(context) is not None
            row.operator('bimux.nucleus_browser', text=label, icon='FILE_FOLDER').kind = kind


def is_plan(context):
    settings = scene_prop(context, 'ifc_storey_toolbar')
    if settings and settings.plan_locked:
        return True
    try:
        module = importlib.import_module('bl_ext.user_default.bonsai_plan_dimensions.overlay')
        return module.is_plan(context)
    except (ImportError, AttributeError, RuntimeError):
        return False


def draw_storey(layout, context, compact=False):
    if operator_exists('ifcstorey.select_storey') and ifc_file():
        layout.operator('bimux.search_storey', text=storey_name(context)[:18], icon='VIEWZOOM')
    elif not compact:
        layout.label(text='Storey: unavailable', icon='INFO')


class BIMUX_OT_search_storey(bpy.types.Operator):
    bl_idname = 'bimux.search_storey'
    bl_label = 'Choose BIM Storey'
    bl_description = 'Search loaded IFC building storeys and set the modeling workplane'
    query: StringProperty(name='Search storeys')

    def invoke(self, context, _event):
        return context.window_manager.invoke_popup(self, width=320)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'query', text='', icon='VIEWZOOM')
        items = [(guid, name) for guid, name, _description in storey_items(self, context)
                 if guid != 'NONE' and self.query.casefold() in name.casefold()]
        if not items:
            layout.label(text='No matching loaded storeys.', icon='INFO')
            return
        tool = ifc_tool()
        file = ifc_file()
        for guid, name in items:
            row = layout.row()
            try:
                row.enabled = tool.Ifc.get_object(file.by_guid(guid)) is not None
            except (AttributeError, RuntimeError, ValueError):
                row.enabled = False
            row.operator('ifcstorey.select_storey', text=name, icon='OUTLINER_COLLECTION').guid = guid

    def execute(self, _context):
        return {'FINISHED'}


def draw_mode(layout, context):
    settings = scene_prop(context, 'ifc_storey_toolbar')
    plan_active = is_plan(context)
    row = layout.row(align=True)
    row.enabled = settings is not None
    if operator_exists('ifcstorey.plan_view') and operator_exists('ifcstorey.view_3d'):
        row.operator('ifcstorey.plan_view', text='Plan', icon='AXIS_TOP',
                     depress=plan_active)
        row.operator('ifcstorey.view_3d', text='3D', icon='VIEW_PERSPECTIVE',
                     depress=not plan_active)
    else:
        row.label(text='Plan / 3D unavailable', icon='INFO')


def active_bim_tool(context):
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        return tool.idname if tool else ''
    except (AttributeError, RuntimeError, TypeError):
        return ''


class BIMUX_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or __name__
    def draw(self, _context):
        self.layout.label(text='Legacy panels are managed by each independent add-on.')
        self.layout.operator('bimux.diagnostics', icon='INFO')


class BIMUX_OT_diagnostics(bpy.types.Operator):
    bl_idname = 'bimux.diagnostics'
    bl_label = 'BIM Integration Diagnostics'
    bl_description = 'Report available optional operators and Blender context'

    def execute(self, _context):
        names = ('ifcstorey.select_storey', 'workplane.set', 'ifcgrid.draw',
                 'bonsai_dynamic_dimension.pick', 'bpd.create',
                 'view3d.bonsai_section_box', 'hemy.copy_global_id',
                 'nucleus.open', 'bonsai_nucleus.start')
        missing = [name for name in names if not operator_exists(name)]
        print('BIM UX Host diagnostics:', 'missing: ' + ', '.join(missing) if missing else 'all mapped operators present')
        self.report({'WARNING' if missing else 'INFO'},
                    f'{len(missing)} mapped operators missing; see System Console' if missing else 'All mapped operators available')
        return {'FINISHED'}


class BIMUX_MT_workplane(bpy.types.Menu):
    bl_idname = 'BIMUX_MT_workplane'
    bl_label = 'Set Workplane'

    def draw(self, _context):
        col = self.layout.column(align=True)
        for label, source, icon in (
            ('From Face', 'FACE', 'FACESEL'), ('From View', 'VIEW', 'VIEW_ORTHO'),
            ('From Cursor', 'CURSOR', 'PIVOT_CURSOR'),
            ('World XY', 'XY', 'AXIS_TOP'), ('World XZ', 'XZ', 'AXIS_FRONT'),
            ('World YZ', 'YZ', 'AXIS_SIDE'),
        ):
            action(col, 'workplane.set', label, icon, source=source)
        action(col, 'workplane.restore', 'Reset Workplane', 'LOOP_BACK')


class BIMUX_MT_tools(bpy.types.Menu):
    bl_idname = 'BIMUX_MT_tools'
    bl_label = 'BIM Tools'

    def draw(self, context):
        layout = self.layout
        for label, tool_id, icon in (
            ('Select', 'builtin.select_box', 'RESTRICT_SELECT_OFF'),
            ('Create', 'bimux.create', 'CUBE'),
            ('Grid', 'bimux.grid', 'MESH_GRID'),
            ('Dimension', 'bimux.dimension', 'DRIVER_DISTANCE'),
            ('Workplane', 'bimux.workplane', 'ORIENTATION_CURSOR'),
            ('Section', 'bimux.section', 'MOD_BOOLEAN'),
            ('Inspect', 'bimux.inspect', 'PROPERTIES'),
        ):
            layout.operator('wm.tool_set_by_id', text=label, icon=icon).name = tool_id


class BIMUX_PT_header_status(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_header_status'
    bl_label = 'BIM Context'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'

    def draw(self, context):
        layout = self.layout
        draw_storey(layout, context)
        layout.label(text='Workplane: ' + workplane_name(context), icon='ORIENTATION_CURSOR')
        layout.menu('BIMUX_MT_workplane', text='Set Workplane')
        draw_mode(layout, context)
        status, icon = nucleus_state(context)
        layout.label(text='Nucleus: ' + status, icon=icon)


def draw_header(self, context):
    layout = self.layout
    layout.separator()
    project, building = context_names(context)
    layout.label(text=f'{project[:12]} > {building[:12]}', icon='FILE_3D')
    draw_storey(layout, context, compact=True)
    layout.menu('BIMUX_MT_workplane', text='WP: ' + workplane_name(context)[:13], icon='ORIENTATION_CURSOR')
    draw_mode(layout, context)
    status, icon = nucleus_state(context)
    layout.popover(panel='BIMUX_PT_nucleus_status', text='Nucleus: ' + status, icon=icon)


class BIMUX_PT_context(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_context'
    bl_label = 'Context'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BIM'
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        file = ifc_file()
        project, building = context_names(context)
        layout.label(text='Project: ' + (bpy.path.basename(bpy.data.filepath) if bpy.data.filepath else ('Unsaved IFC' if file else 'No IFC project')))
        if file:
            layout.label(text='IFC: ' + project[:45], icon='FILE_3D')
            layout.label(text='Building: ' + building[:40], icon='OUTLINER_COLLECTION')
        draw_storey(layout, context)
        layout.label(text='Workplane: ' + workplane_name(context), icon='ORIENTATION_CURSOR')
        layout.menu('BIMUX_MT_workplane', text='Set Workplane', icon='ORIENTATION_CURSOR')
        draw_mode(layout, context)


class BIMUX_PT_active(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_active'
    bl_label = 'Active Tool'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BIM'
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        layout.menu('BIMUX_MT_tools', text='Choose BIM Tool', icon='TOOL_SETTINGS')
        tool_id = active_bim_tool(context)
        entity = ifc_element(context)
        if tool_id == 'bimux.grid':
            layout.label(text='Grid', icon='MESH_GRID')
            action(layout, 'ifcgrid.create', 'Create Grid', 'ADD')
            action(layout, 'ifcgrid.draw', 'Draw Grid Line', 'MESH_GRID')
            action(layout, 'ifcgrid.offset', 'Offset Axis', 'MOD_ARRAY')
            action(layout, 'ifcgrid.rename', 'Rename Axis', 'GREASEPENCIL')
            settings = scene_prop(context, 'ifc_grid_toolbar')
            if settings:
                layout.prop(settings, 'axis_tag')
                layout.prop(settings, 'offset')
                layout.prop(settings, 'snap')
        elif tool_id == 'bimux.dimension':
            plan = is_plan(context)
            action(layout, 'bonsai_dynamic_dimension.pick', 'Reference Dimension', 'DRIVER_DISTANCE', enabled=plan)
            action(layout, 'bpd.create', 'Driving Dimension', 'DRIVER_DISTANCE', enabled=plan)
            if not plan:
                layout.label(text='Available in plan orthographic view.', icon='INFO')
            else:
                layout.label(text='Reference measures until Edit Value is used.', icon='INFO')
                layout.label(text='Edit Value in the legacy panel can move a wall.', icon='ERROR')
                layout.label(text='Driving edits move associated IFC elements.', icon='ERROR')
                settings = scene_prop(context, 'BPDProperties')
                if settings:
                    layout.prop(settings, 'wall_mode')
                    layout.prop(settings, 'offset')
        elif tool_id == 'bimux.workplane':
            layout.label(text='Current: ' + workplane_name(context), icon='ORIENTATION_CURSOR')
            layout.menu('BIMUX_MT_workplane', text='Set Workplane')
        elif tool_id == 'bimux.section':
            action(layout, 'view3d.bonsai_section_box', 'Fit Section Box', 'MOD_BOOLEAN', enabled=bool(context.selected_objects))
            action(layout, 'view3d.bonsai_section_box_clear', 'Reset Section Box', 'X')
        elif tool_id == 'bimux.inspect':
            action(layout, 'bimux.show_ifc_properties', 'Open IFC Properties', 'PROPERTIES', enabled=bool(entity))
            layout.label(text='Class Visibility is in View.', icon='FILTER')
        elif tool_id == 'bimux.create':
            layout.label(text='Choose an IFC element in the Bonsai toolbar.', icon='CUBE')
            if available('hemy_ifc_panel'):
                layout.label(text='Photo wall builder is below.', icon='FILE_IMAGE')
            if entity and entity.is_a() in ('IfcWall', 'IfcSlab', 'IfcColumn'):
                draw_type_dimensions(layout, entity)
        elif entity and entity.is_a() in ('IfcWall', 'IfcSlab', 'IfcColumn'):
            layout.label(text='Selected IFC type', icon='DRIVER_DISTANCE')
            draw_type_dimensions(layout, entity)
        else:
            layout.label(text='Choose a task tool at the left.', icon='INFO')


class BIMUX_PT_photo_wall(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_photo_wall'
    bl_label = 'Create Wall from Photo'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BIM'
    bl_parent_id = 'BIMUX_PT_active'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, _context):
        return available('hemy_ifc_panel')

    def draw(self, context):
        try:
            module = importlib.import_module('hemy_ifc_panel.wall_authoring')
            if not scene_prop(context, 'hemy_wall_authoring'):
                self.layout.label(text='Wall builder unavailable.', icon='INFO')
                return
            module.HEMY_PT_wall_authoring.draw(self, context)
        except (ImportError, AttributeError, RuntimeError):
            self.layout.label(text='Enable the Hemy wall builder.', icon='INFO')


def draw_type_dimensions(layout, entity):
    ifc_type = selected_type(entity)
    if not ifc_type:
        layout.label(text='Assign an IFC type to edit its dimensions.', icon='INFO')
        return
    layout.label(text=ifc_type.Name or ifc_type.is_a())
    class_name = entity.is_a()
    idname = {'IfcWall': 'bim.wall_type_thickness',
              'IfcSlab': 'bim.slab_thickness',
              'IfcColumn': 'bim.column_type_profile_size'}.get(class_name)
    if idname:
        action(layout, idname, 'Edit Type Dimensions', 'DRIVER_DISTANCE', type_id=ifc_type.id())
        layout.label(text='Changes every occurrence of this type.', icon='INFO')


class BIMUX_PT_selection(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_selection'
    bl_label = 'Selection'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BIM'
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        entity = ifc_element(context)
        if not entity:
            layout.label(text='Select an IFC element.', icon='INFO')
            return
        layout.label(text=entity.is_a(), icon='OUTLINER_OB_MESH')
        layout.label(text=(entity.Name or 'Unnamed')[:42])
        ifc_type = selected_type(entity)
        layout.label(text='Type: ' + ((ifc_type.Name or ifc_type.is_a()) if ifc_type else 'None'))
        guid = getattr(entity, 'GlobalId', '') or ''
        row = layout.row(align=True)
        row.label(text='GlobalId: ' + guid[:16] + ('…' if len(guid) > 16 else ''))
        if operator_exists('hemy.copy_global_id'):
            row.operator('hemy.copy_global_id', text='', icon='COPYDOWN').value = guid
        layout.label(text='Storey: ' + element_storey_name(entity))
        draw_type_dimensions(layout, entity)
        action(layout, 'bimux.show_ifc_properties', 'Open Full IFC Properties', 'PROPERTIES')


class BIMUX_OT_show_ifc_properties(bpy.types.Operator):
    bl_idname = 'bimux.show_ifc_properties'
    bl_label = 'Open Full IFC Properties'

    def execute(self, context):
        for area in context.screen.areas:
            if area.type == 'PROPERTIES':
                area.spaces.active.context = 'OBJECT'
                return {'FINISHED'}
        self.report({'INFO'}, 'Open a Properties Editor to see IFC Properties')
        return {'CANCELLED'}


class BIMUX_PT_view(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_view'
    bl_label = 'View'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BIM'
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        action(layout, 'view3d.bonsai_section_box', 'Fit Section Box to Selection', 'MOD_BOOLEAN', enabled=bool(context.selected_objects))
        action(layout, 'view3d.bonsai_section_box_clear', 'Reset Section Box', 'X')
        action(layout, 'view3d.localview', 'Isolate Selection', 'HIDE_OFF', enabled=bool(context.selected_objects))
        action(layout, 'view3d.localview', 'Show All', 'HIDE_ON')
        layout.label(text='If view says Clipped, press Alt+B in viewport.', icon='INFO')
        if available('hemy_ifc_panel'):
            layout.label(text='Class Visibility', icon='FILTER')
            try:
                mod = importlib.import_module('hemy_ifc_panel')
                mod.HEMY_PT_class_visibility.draw(self, context)
            except (ImportError, AttributeError, RuntimeError):
                layout.label(text='Class controls unavailable', icon='INFO')
        if context.space_data and context.space_data.type == 'VIEW_3D':
            layout.prop(context.space_data.overlay, 'show_overlays', text='Show Overlays')


class BIMUX_PT_collaboration(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_collaboration'
    bl_label = 'Collaboration'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BIM'
    bl_order = 4
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        status, icon = nucleus_state(context)
        layout.label(text='Nucleus: ' + status, icon=icon)
        settings = scene_prop(context, 'bonsai_nucleus_sync')
        if settings and settings.status:
            layout.label(text=settings.status[:42])
        layout.label(text='Files', icon='FILE_FOLDER')
        if nucleus_browser_state(context) is not None:
            for kind, label in (('IFC', 'IFC projects'), ('BLEND', 'Blend projects')):
                layout.operator('bimux.nucleus_browser', text=label, icon='FILE_FOLDER').kind = kind
            layout.label(text='Open and Save are separate in the browser.')
        else:
            layout.label(text='Nucleus Files unavailable', icon='INFO')
        if status == 'Offline':
            layout.label(text='Live Sync is offline.', icon='UNLINKED')
            layout.label(text='Configure in Bonsai Sync before connecting.')
        else:
            action(layout, 'bonsai_nucleus.stop', 'Leave Live Sync', 'CANCEL')


class BIMUX_PT_properties(bpy.types.Panel):
    bl_idname = 'BIMUX_PT_properties'
    bl_label = 'IFC Properties'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'object'

    def draw(self, context):
        if not available('hemy_ifc_panel'):
            self.layout.label(text='Enable Hemy IFC Properties.', icon='INFO')
            return
        try:
            mod = importlib.import_module('hemy_ifc_panel')
            mod.HEMY_PT_element_properties.draw(self, context)
        except (ImportError, AttributeError, RuntimeError):
            self.layout.label(text='IFC properties unavailable', icon='INFO')


class BIMUX_WST_create(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = 'bimux.create'
    bl_label = 'Create'
    bl_description = 'Bonsai wall, slab, column and other IFC creation tools'
    bl_icon = 'ops.mesh.primitive_cube_add_gizmo'
    bl_keymap = ()

    def draw_settings(context, layout, tool):
        if operator_exists('wm.tool_set_by_id'):
            layout.operator('wm.tool_set_by_id', text='Bonsai Create', icon='CUBE').name = 'bim.bim_tool'
        else:
            layout.label(text='Use Bonsai creation tools below', icon='INFO')


class BIMUX_WST_grid(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = 'bimux.grid'
    bl_label = 'Grid'
    bl_description = 'Draw, add, offset and rename IFC grid axes'
    bl_icon = 'ops.generic.cursor'
    bl_keymap = ()

    def draw_settings(context, layout, tool):
        action(layout, 'ifcgrid.draw', 'Draw Grid', 'MESH_GRID')
        action(layout, 'ifcgrid.add_axis', 'Add Axis', 'ADD')
        action(layout, 'ifcgrid.offset', 'Offset Axis', 'MOD_ARRAY')
        action(layout, 'ifcgrid.rename', 'Rename Axis', 'GREASEPENCIL')


class BIMUX_WST_dimension(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = 'bimux.dimension'
    bl_label = 'Dimension'
    bl_description = 'Reference and driving dimensions in orthographic plan view'
    bl_icon = 'ops.transform.transform'
    bl_keymap = ()

    def draw_settings(context, layout, tool):
        plan = is_plan(context)
        action(layout, 'bonsai_dynamic_dimension.pick', 'Reference Dimension', 'DRIVER_DISTANCE', enabled=plan)
        action(layout, 'bpd.create', 'Driving Dimension', 'DRIVER_DISTANCE', enabled=plan)
        if not plan:
            layout.label(text='Available in plan orthographic view', icon='INFO')
        else:
            layout.label(text='Editing either value can move IFC elements', icon='ERROR')


class BIMUX_WST_workplane(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = 'bimux.workplane'
    bl_label = 'Workplane'
    bl_description = 'Set the active modeling plane'
    bl_icon = 'ops.generic.select_circle'
    bl_keymap = ()

    def draw_settings(context, layout, tool):
        layout.menu('BIMUX_MT_workplane', text='Set Workplane', icon='ORIENTATION_CURSOR')


class BIMUX_WST_section(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = 'bimux.section'
    bl_label = 'Section'
    bl_description = 'Fit and reset a viewport section box'
    bl_icon = 'ops.mesh.bisect'
    bl_keymap = ()

    def draw_settings(context, layout, tool):
        action(layout, 'view3d.bonsai_section_box', 'Fit Section Box', 'MOD_BOOLEAN', enabled=bool(context.selected_objects))
        action(layout, 'view3d.bonsai_section_box_clear', 'Reset Section Box', 'X')


class BIMUX_WST_inspect(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = 'bimux.inspect'
    bl_label = 'Inspect'
    bl_description = 'Inspect IFC properties and review visibility'
    bl_icon = 'ops.generic.select_box'
    bl_keymap = ()

    def draw_settings(context, layout, tool):
        action(layout, 'bimux.show_ifc_properties', 'IFC Properties', 'PROPERTIES')
        action(layout, 'view3d.localview', 'Isolate Selection', 'HIDE_OFF', enabled=bool(context.selected_objects))
        layout.label(text='Class Visibility: BIM sidebar', icon='FILTER')


CLASSES = (BIMUX_Preferences, BIMUX_OT_diagnostics, BIMUX_OT_show_ifc_properties,
           BIMUX_OT_search_storey, BIMUX_OT_nucleus_browser,
           BIMUX_MT_workplane, BIMUX_MT_tools, BIMUX_MT_nucleus_files,
           BIMUX_PT_header_status, BIMUX_PT_nucleus_status,
           BIMUX_PT_context, BIMUX_PT_active, BIMUX_PT_photo_wall, BIMUX_PT_selection,
           BIMUX_PT_view, BIMUX_PT_collaboration, BIMUX_PT_properties)

TOOLS = (BIMUX_WST_create, BIMUX_WST_grid, BIMUX_WST_dimension,
         BIMUX_WST_workplane, BIMUX_WST_section, BIMUX_WST_inspect)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_header.prepend(draw_header)
    bpy.types.TOPBAR_MT_file.append(draw_file_menu)
    previous = 'builtin.select_box'
    for cls in TOOLS:
        bpy.utils.register_tool(cls, after={previous}, separator=cls is TOOLS[0])
        previous = cls.bl_idname


def unregister():
    for cls in reversed(TOOLS):
        bpy.utils.unregister_tool(cls)
    bpy.types.VIEW3D_HT_header.remove(draw_header)
    bpy.types.TOPBAR_MT_file.remove(draw_file_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
