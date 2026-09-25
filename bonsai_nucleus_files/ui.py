import json
import os
from pathlib import Path
import textwrap
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

from . import bridge, native
from .core import (DEFAULT_FOLDER, NucleusError, normalize_url, parent_url, child_url,
                   extension, validate_native, filename, local_name, write_json)

PACKAGE = __package__
_job = None
_completion = None
_pending_open = None
_opening = False


def preferences():
    addon = bpy.context.preferences.addons.get(PACKAGE)
    return addon.preferences if addon else None


def settings():
    prefs = preferences()
    python, sdk = bridge.discover()
    cache = os.environ.get("BONSAI_NUCLEUS_CACHE") or bpy.utils.user_resource("DATAFILES", path="bonsai_nucleus_files", create=True)
    if prefs:
        python = bpy.path.abspath(prefs.python_path) if prefs.python_path else python
        sdk = bpy.path.abspath(prefs.sdk_path) if prefs.sdk_path else sdk
        cache = bpy.path.abspath(prefs.cache_path) if prefs.cache_path else cache
    return bridge.Settings(python, sdk, str(Path(cache).resolve()), prefs.timeout if prefs else 180)


def state():
    return bpy.context.window_manager.nucleus_files


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def status(message, error=False):
    s = state()
    s.status = str(message)
    s.error = error
    redraw()


def index_path():
    return Path(settings().cache) / "working-copies.json"


def records():
    try:
        return json.loads(index_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_key(path):
    return str(Path(path).resolve()).casefold()


def remember(data):
    items = records()
    items[record_key(data["local"])] = data
    write_json(index_path(), items)


def current_record(kind):
    path = bpy.data.filepath if kind == "BLEND" else native.ifc_path()
    return records().get(record_key(path)) if path else None


def new_local(url):
    path = Path(settings().cache) / "working" / uuid.uuid4().hex / local_name(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def start_job(payload, callback):
    global _job, _completion
    if _job is not None:
        raise NucleusError("Wait for the current transfer, or cancel it first.")
    prefs = preferences()
    if hasattr(bpy.app, "online_access") and not bpy.app.online_access:
        raise NucleusError("Enable Allow Online Access in Blender's system preferences.")
    _job = bridge.Job(settings(), payload)
    _completion = callback
    state().busy = True
    status({"list": "Connecting to Nucleus… Complete browser sign-in if prompted.",
            "download": "Downloading a local working copy…",
            "upload": "Saving to Nucleus… Local recovery file is retained."}.get(payload["op"], "Working…"))
    if not bpy.app.timers.is_registered(poll_job):
        bpy.app.timers.register(poll_job, first_interval=0.2, persistent=True)


def poll_job():
    global _job, _completion
    if _job is None:
        return None
    result = _job.poll()
    if result is None:
        return 0.2
    done = _completion
    _job = _completion = None
    state().busy = False
    try:
        if not result["ok"]:
            status(result["error"], True)
        elif done:
            done(result["data"])
    except Exception as exc:
        status(str(exc), True)
    return 0.2 if _job is not None else None


def cancel_job():
    global _job, _completion
    if _job is not None:
        uploading = _job.payload["op"] == "upload"
        _job.cancel()
        _job = _completion = None
        state().busy = False
        status("Transfer stopped. Upload outcome may be unknown; refresh before retrying. Recovery file retained."
               if uploading else "Operation cancelled.", uploading)


def apply_listing(data):
    s = state()
    s.folder = data["url"]
    s.listed_folder = data["url"]
    s.entries.clear()
    for entry in data["items"]:
        item = s.entries.add()
        item.name = entry["name"]
        item.url = entry["url"]
        item.is_folder = entry["folder"]
        item.writable = entry["write"]
        item.size_label = "Folder" if entry["folder"] else format_size(entry["size"])
        item.native = entry["folder"] or extension(entry["url"]) in {".ifc", ".ifczip", ".blend"}
    s.selected = 0
    status(f"Connected · {len(data['items'])} items")


def format_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def selected_entry():
    s = state()
    if s.entries and 0 <= s.selected < len(s.entries):
        return s.entries[s.selected]
    raise NucleusError("Select a folder or native file in the browser.")


def downloaded(data):
    global _pending_open
    _pending_open = data
    state().recovery_path = data["local"]
    status("Download complete. Confirm Open to replace the current project.")
    bpy.ops.nucleus.open_downloaded("INVOKE_DEFAULT")


def uploaded(data):
    remember(data)
    state().recovery_path = data["local"]
    state().folder = parent_url(data["url"])
    status(data.get("warning") or f"Saved to Nucleus · {filename(data['url'])}", bool(data.get("warning")))


def save_project(url, expected, pack_resources=True):
    url = validate_native(url)
    path = new_local(url)
    state().recovery_path = str(path)
    # Native serialization must execute on Blender's main thread before starting the worker.
    if extension(url) == ".blend":
        native.save_blend(path, pack_resources)
    else:
        native.save_ifc(path)
    # Remember the baseline immediately so a failed upload is safely retryable.
    remember({"url": url, "local": str(path), "revision": expected, "pending": True})
    start_job({"op": "upload", "url": url, "local": str(path), "expected": expected}, uploaded)


class NUCLEUS_Preferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE
    python_path: StringProperty(name="SDK Python executable", subtype="FILE_PATH")
    sdk_path: StringProperty(name="NVIDIA SDK folder", subtype="DIR_PATH")
    cache_path: StringProperty(name="Working copies folder", subtype="DIR_PATH")
    home_folder: StringProperty(name="Home folder", default=DEFAULT_FOLDER)
    timeout: IntProperty(name="Transfer timeout (seconds)", default=180, min=30, max=7200)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Connect using NVIDIA Client Library and its compatible Python runtime.")
        layout.operator("nucleus.detect_runtime", icon="VIEWZOOM")
        layout.prop(self, "python_path")
        layout.prop(self, "sdk_path")
        layout.prop(self, "cache_path")
        layout.prop(self, "home_folder")
        layout.prop(self, "timeout")
        layout.label(text="Authentication uses NVIDIA's cached login or browser sign-in.", icon="INFO")


class NUCLEUS_Entry(bpy.types.PropertyGroup):
    url: StringProperty()
    is_folder: BoolProperty()
    writable: BoolProperty()
    native: BoolProperty()
    size_label: StringProperty()


class NUCLEUS_State(bpy.types.PropertyGroup):
    folder: StringProperty(name="Nucleus folder", default=DEFAULT_FOLDER, options={"SKIP_SAVE"})
    listed_folder: StringProperty(options={"SKIP_SAVE"})
    entries: CollectionProperty(type=NUCLEUS_Entry, options={"SKIP_SAVE"})
    selected: IntProperty(default=0, options={"SKIP_SAVE"})
    status: StringProperty(default="Connect to browse native projects.", options={"SKIP_SAVE"})
    error: BoolProperty(options={"SKIP_SAVE"})
    busy: BoolProperty(options={"SKIP_SAVE"})
    kind: EnumProperty(name="Project", items=[("IFC", "IFC project", "Bonsai's native BIM model"),
                                              ("BLEND", "Blender project", "Blender scene with embedded active IFC snapshot")])
    pack_resources: BoolProperty(name="Pack supported .blend resources", default=True)
    recovery_path: StringProperty(name="Local recovery file", subtype="FILE_PATH", options={"SKIP_SAVE"})


class SafeOperator:
    @classmethod
    def poll(cls, context):
        return _job is None

    def execute(self, context):
        try:
            return self.run(context) or {"FINISHED"}
        except Exception as exc:
            status(str(exc), True)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class NUCLEUS_OT_detect(SafeOperator, bpy.types.Operator):
    bl_idname = "nucleus.detect_runtime"
    bl_label = "Detect Runtime"

    def run(self, context):
        python, sdk = bridge.discover()
        if not python or not sdk:
            raise NucleusError("No runtime detected. Set SDK Python and NVIDIA SDK folder manually; see the setup guide.")
        prefs = preferences()
        if prefs:
            prefs.python_path, prefs.sdk_path = python, sdk
        status("NVIDIA runtime detected. Connect to verify your login.")


class NUCLEUS_OT_refresh(SafeOperator, bpy.types.Operator):
    bl_idname = "nucleus.refresh"
    bl_label = "Connect / Refresh"
    bl_description = "Browse this Nucleus folder using your NVIDIA login"

    def run(self, context):
        url = normalize_url(state().folder, True)
        start_job({"op": "list", "url": url}, apply_listing)


class NUCLEUS_OT_navigate(SafeOperator, bpy.types.Operator):
    bl_idname = "nucleus.navigate"
    bl_label = "Browse Folder"
    direction: EnumProperty(items=[("UP", "Up", ""), ("HOME", "Home", ""), ("SELECTED", "Selected", "")])

    def run(self, context):
        s = state()
        if self.direction == "UP":
            url = parent_url(s.folder)
        elif self.direction == "HOME":
            url = preferences().home_folder if preferences() else DEFAULT_FOLDER
        else:
            item = selected_entry()
            if not item.is_folder:
                raise NucleusError("Select a folder to enter.")
            url = item.url
        start_job({"op": "list", "url": normalize_url(url, True)}, apply_listing)


class NUCLEUS_OT_open(SafeOperator, bpy.types.Operator):
    bl_idname = "nucleus.open"
    bl_label = "Open from Nucleus"
    bl_description = "Download and open the selected native file"

    def run(self, context):
        item = selected_entry()
        if item.is_folder:
            start_job({"op": "list", "url": normalize_url(item.url, True)}, apply_listing)
            return
        url = validate_native(item.url)
        if extension(url) != ".blend":
            native.bonsai()
        start_job({"op": "download", "url": url, "local": str(new_local(url))}, downloaded)


class NUCLEUS_OT_open_downloaded(SafeOperator, bpy.types.Operator):
    bl_idname = "nucleus.open_downloaded"
    bl_label = "Open Downloaded Project"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=480, confirm_text="Open")

    def draw(self, context):
        self.layout.label(text=filename(_pending_open["url"]) if _pending_open else "No pending download")
        self.layout.label(text="Opening replaces the current project and discards unsaved edits.", icon="ERROR")
        self.layout.label(text="Cancel and save your work first if needed.")

    def run(self, context):
        global _opening, _pending_open
        if not _pending_open:
            raise NucleusError("No downloaded project is waiting to open.")
        data = _pending_open
        _pending_open = None
        _opening = True
        try:
            native.open_native(data["local"])
            remember(data)
            state().kind = "BLEND" if extension(data["url"]) == ".blend" else "IFC"
            state().folder = parent_url(data["url"])
            state().recovery_path = data["local"]
            if not state().error:
                status(f"Opened from Nucleus · {filename(data['url'])}")
        finally:
            _opening = False


class NUCLEUS_OT_save(SafeOperator, bpy.types.Operator):
    bl_idname = "nucleus.save"
    bl_label = "Save to Nucleus"
    bl_description = "Save the current project back to its Nucleus file; refuse conflicting changes"

    def run(self, context):
        s = state()
        record = current_record(s.kind)
        if not record:
            return bpy.ops.nucleus.save_as("INVOKE_DEFAULT")
        save_project(record["url"], record["revision"], s.pack_resources)


class NUCLEUS_OT_save_as(SafeOperator, bpy.types.Operator):
    bl_idname = "nucleus.save_as"
    bl_label = "Save As to Nucleus"
    folder: StringProperty(name="Nucleus folder")
    name: StringProperty(name="Filename", default="Project.ifc")
    kind: EnumProperty(name="Format", items=[("IFC", "IFC (.ifc)", ""), ("IFCZIP", "IFC ZIP (.ifczip)", ""),
                                            ("BLEND", "Blender (.blend)", "")])

    def invoke(self, context, event):
        self.folder = state().folder
        self.kind = "BLEND" if state().kind == "BLEND" else "IFC"
        path = bpy.data.filepath if self.kind == "BLEND" else native.ifc_path()
        self.name = Path(path).stem if path else "Project"
        return context.window_manager.invoke_props_dialog(self, width=540, confirm_text="Save to Nucleus")

    def draw(self, context):
        self.layout.prop(self, "folder")
        self.layout.prop(self, "name")
        self.layout.prop(self, "kind")
        self.layout.label(text="Use a new filename. Existing files are protected from replacement.", icon="INFO")

    def run(self, context):
        suffix = {"IFC": ".ifc", "IFCZIP": ".ifczip", "BLEND": ".blend"}[self.kind]
        name = self.name.strip()
        if not name:
            raise NucleusError("Enter a filename.")
        if Path(name).suffix.lower() in {".blend", ".ifc", ".ifczip"}:
            name = name[: -len(Path(name).suffix)]
        url = child_url(self.folder, name + suffix)
        state().kind = "BLEND" if self.kind == "BLEND" else "IFC"
        save_project(url, None, state().pack_resources)


class NUCLEUS_OT_cancel(bpy.types.Operator):
    bl_idname = "nucleus.cancel"
    bl_label = "Cancel Transfer"

    def execute(self, context):
        cancel_job()
        return {"FINISHED"}


class NUCLEUS_UL_files(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.enabled = item.native
        row.label(text=item.name, icon="FILE_FOLDER" if item.is_folder else "FILE_BLEND" if extension(item.url) == ".blend" else "FILE_3D")
        row.label(text=item.size_label)
        if not item.writable:
            row.label(text="", icon="LOCKED")


class NUCLEUS_PT_browser(bpy.types.Panel):
    bl_label = "Nucleus Files"
    bl_idname = "NUCLEUS_PT_browser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Nucleus"

    def draw(self, context):
        s = state()
        layout = self.layout
        column = layout.column()
        column.enabled = not s.busy
        column.prop(s, "folder", text="")
        row = column.row(align=True)
        row.operator("nucleus.navigate", text="", icon="HOME").direction = "HOME"
        row.operator("nucleus.navigate", text="", icon="FILE_PARENT").direction = "UP"
        row.operator("nucleus.refresh", icon="FILE_REFRESH")
        column.template_list("NUCLEUS_UL_files", "", s, "entries", s, "selected", rows=7)
        if s.listed_folder:
            try:
                changed = normalize_url(s.folder, True) != s.listed_folder
            except (NucleusError, ValueError):
                changed = True
            if changed:
                column.label(text="Refresh to show the entered folder.", icon="INFO")
        column.operator("nucleus.open", icon="FILE_FOLDER")
        if _pending_open:
            column.operator("nucleus.open_downloaded", text="Open Downloaded Project…")
        column.separator()
        column.prop(s, "kind", expand=True)
        if s.kind == "BLEND":
            column.prop(s, "pack_resources")
            if native.has_ifc():
                column.label(text="Active IFC will be embedded in .blend.", icon="CHECKMARK")
        row = column.row(align=True)
        row.operator("nucleus.save", text="Save", icon="FILE_TICK")
        row.operator("nucleus.save_as", text="Save As…", icon="FILE_NEW")
        try:
            record = current_record(s.kind)
        except Exception:
            record = None
        if record:
            column.label(text="Linked: " + filename(record["url"]), icon="LINKED")
        box = layout.box()
        box.alert = s.error
        for line in textwrap.wrap(s.status, width=max(25, int(context.region.width / 7))):
            box.label(text=line)
        if s.busy:
            layout.operator("nucleus.cancel", icon="CANCEL")
        if s.recovery_path:
            layout.prop(s, "recovery_path", text="Local copy")


class NUCLEUS_MT_files(bpy.types.Menu):
    bl_label = "Nucleus"

    def draw(self, context):
        self.layout.operator("nucleus.refresh", text="Connect / Refresh Browser")
        self.layout.operator("nucleus.open")
        self.layout.separator()
        self.layout.operator("nucleus.save")
        self.layout.operator("nucleus.save_as")


def file_menu(self, context):
    self.layout.separator()
    self.layout.menu("NUCLEUS_MT_files", icon="NETWORK_DRIVE")


@persistent
def on_load(_):
    global _pending_open
    if not _opening:
        cancel_job()
        _pending_open = None
    state().busy = False
    state().entries.clear()
    state().listed_folder = ""
    status("Connect to browse native projects.")
    try:
        if native.restore_embedded_ifc(settings().cache):
            status("Restored the embedded IFC snapshot from this Blender project.")
    except Exception as exc:
        status(f"Blender file opened, but IFC restore failed: {exc}", True)


CLASSES = (NUCLEUS_Preferences, NUCLEUS_Entry, NUCLEUS_State, NUCLEUS_OT_detect,
           NUCLEUS_OT_refresh, NUCLEUS_OT_navigate, NUCLEUS_OT_open,
           NUCLEUS_OT_open_downloaded, NUCLEUS_OT_save, NUCLEUS_OT_save_as,
           NUCLEUS_OT_cancel, NUCLEUS_UL_files, NUCLEUS_PT_browser, NUCLEUS_MT_files)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.nucleus_files = PointerProperty(type=NUCLEUS_State)
    bpy.types.TOPBAR_MT_file.append(file_menu)
    # Restore the IFC before Bonsai's load_post tries to read its local project path.
    bpy.app.handlers.load_post.insert(0, on_load)


def unregister():
    cancel_job()
    if bpy.app.timers.is_registered(poll_job):
        bpy.app.timers.unregister(poll_job)
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
    bpy.types.TOPBAR_MT_file.remove(file_menu)
    del bpy.types.WindowManager.nucleus_files
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
