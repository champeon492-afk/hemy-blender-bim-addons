"""Bonsai Nucleus Live Sync: Blender UI and lifecycle."""
bl_info = {
    'name': 'Bonsai Nucleus Live Sync', 'author': 'Bonsai Live Sync contributors',
    'version': (0, 1, 0), 'blender': (4, 2, 0), 'location': '3D View > Sidebar > Bonsai Sync',
    'description': 'Send Blender and Bonsai edits to an Omniverse Nucleus Live Session',
    'category': 'Import-Export',
}

import queue
import time
import uuid
import bpy
from bpy.app.handlers import persistent
from bpy.props import FloatProperty, IntProperty, PointerProperty, StringProperty
from .core.protocol import DEFAULT_STAGE, stage_url
from .exporter import Exporter
from .transport import Transport

_runtime = None


class SyncSettings(bpy.types.PropertyGroup):
    target: StringProperty(name='USD stage', default=DEFAULT_STAGE)
    port: IntProperty(name='Receiver port', default=8211, min=1024, max=65535)
    token: StringProperty(name='Receiver token', subtype='PASSWORD', options={'SKIP_SAVE'})
    interval: FloatProperty(name='Update interval (s)', default=0.15, min=0.05, max=5.0)
    collection: PointerProperty(name='Sync collection', type=bpy.types.Collection)
    source: StringProperty(options={'HIDDEN'})
    status: StringProperty(default='Stopped', options={'SKIP_SAVE'})


class Runtime:
    def __init__(self, context):
        self.scene = context.scene
        settings = self.scene.bonsai_nucleus_sync
        self.target = stage_url(settings.target)
        if len(settings.token) < 24:
            raise ValueError('Copy the token from the Bonsai Nucleus Sync window in Omniverse.')
        if not settings.source:
            settings.source = uuid.uuid4().hex
        self.source = settings.source
        self.exporter = Exporter()
        self.transport = Transport(settings.port, settings.token)
        self.stream = uuid.uuid4().hex
        self.seq = 0
        self.baseline = None
        self.pending = None
        self.pending_scale = None
        self.ack_scale = None
        self.resync_requested = False
        self.retry_at = 0
        self.sample_at = 0
        self.last_send = 0
        self.sampling = False

    def tick(self):
        settings = self.scene.bonsai_nucleus_sync
        now = time.monotonic()
        try:
            success, result = self.transport.results.get_nowait()
            if success:
                self.baseline = self.pending
                self.ack_scale = self.pending_scale
                settings.status = 'Live | update %d | %d objects' % (self.seq, len(self.baseline))
            else:
                # Reset the stream and send an authoritative snapshot after any
                # uncertain acknowledgement, receiver restart, or network failure.
                self.baseline = None
                self.stream, self.seq = uuid.uuid4().hex, 0
                self.retry_at = now + 2
                settings.status = 'Retrying: ' + str(result)[:220]
            self.pending = None
        except queue.Empty:
            pass
        if self.pending is not None or now < self.retry_at or now < self.sample_at:
            return
        if bpy.context.scene != self.scene:
            stop('Stopped: scene changed')
            return
        if self.resync_requested:
            self.baseline = None
            self.stream, self.seq = uuid.uuid4().hex, 0
            self.exporter.dirty()
            self.resync_requested = False
        self.sample_at = now + settings.interval
        try:
            self.sampling = True
            updates, removed, state = self.exporter.capture(bpy.context, self.baseline, settings.collection)
            scale = self.scene.unit_settings.scale_length
            if not updates and not removed and self.baseline is not None and scale == self.ack_scale:
                if now - self.last_send < 2:
                    return
                # Heartbeats discover receiver restarts even while Blender is idle.
            packet = {'version': 1, 'source': self.source, 'stream': self.stream, 'seq': self.seq + 1,
                      'full': self.baseline is None, 'stage': self.target, 'meters_per_unit': scale,
                      'objects': updates, 'removed': removed}
            self.transport.submit(packet)
            self.seq += 1
            self.pending, self.pending_scale = state, scale
            self.last_send = now
            settings.status = 'Sending update %d...' % self.seq
        except Exception as exc:
            settings.status = 'Export error: ' + str(exc)[:220]
            self.retry_at = now + 2
        finally:
            self.sampling = False


def stop(message='Stopped; live edits remain in the Omniverse session'):
    global _runtime
    if _runtime:
        runtime, _runtime = _runtime, None
        runtime.transport.close()
        try:
            runtime.scene.bonsai_nucleus_sync.status = message
        except ReferenceError:
            pass


def timer():
    if not _runtime:
        return None
    try:
        _runtime.tick()
    except Exception as exc:
        stop('Stopped: ' + str(exc)[:220])
        return None
    return 0.05 if _runtime else None


@persistent
def on_depsgraph(scene, depsgraph):
    if _runtime and not _runtime.sampling and scene == _runtime.scene:
        _runtime.exporter.dirty(depsgraph)


@persistent
def on_history(*args):
    if _runtime:
        _runtime.resync_requested = True


@persistent
def on_load(*args):
    stop('Stopped: file changed')


class StartSync(bpy.types.Operator):
    bl_idname = 'bonsai_nucleus.start'
    bl_label = 'Start Live Sync'

    def execute(self, context):
        global _runtime
        if _runtime:
            return {'CANCELLED'}
        try:
            _runtime = Runtime(context)
            if not bpy.app.timers.is_registered(timer):
                bpy.app.timers.register(timer, first_interval=0.05)
            context.scene.bonsai_nucleus_sync.status = 'Connecting...'
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class StopSync(bpy.types.Operator):
    bl_idname = 'bonsai_nucleus.stop'
    bl_label = 'Stop Sync'

    def execute(self, context):
        stop()
        return {'FINISHED'}


class Resync(bpy.types.Operator):
    bl_idname = 'bonsai_nucleus.resync'
    bl_label = 'Resync All'

    def execute(self, context):
        if _runtime:
            _runtime.resync_requested = True
        return {'FINISHED'}


class SyncPanel(bpy.types.Panel):
    bl_label = 'Bonsai Nucleus Live Sync'
    bl_idname = 'BONSAI_NUCLEUS_PT_sync'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Bonsai Sync'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.bonsai_nucleus_sync
        col = layout.column()
        col.enabled = _runtime is None
        col.prop(settings, 'target')
        col.prop(settings, 'port')
        col.prop(settings, 'token')
        col.prop(settings, 'collection')
        layout.prop(settings, 'interval')
        if _runtime:
            row = layout.row()
            row.operator(StopSync.bl_idname)
            row.operator(Resync.bl_idname)
        else:
            layout.operator(StartSync.bl_idname, icon='PLAY')
        layout.label(text=settings.status)
        layout.label(text='Receiver must be running in Omniverse.')


CLASSES = (SyncSettings, StartSync, StopSync, Resync, SyncPanel)
HANDLERS = ((bpy.app.handlers.depsgraph_update_post, on_depsgraph),
            (bpy.app.handlers.undo_post, on_history), (bpy.app.handlers.redo_post, on_history),
            (bpy.app.handlers.load_pre, on_load))


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bonsai_nucleus_sync = PointerProperty(type=SyncSettings)
    for handlers, function in HANDLERS:
        if function not in handlers:
            handlers.append(function)


def unregister():
    stop()
    if bpy.app.timers.is_registered(timer):
        bpy.app.timers.unregister(timer)
    for handlers, function in HANDLERS:
        if function in handlers:
            handlers.remove(function)
    del bpy.types.Scene.bonsai_nucleus_sync
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
