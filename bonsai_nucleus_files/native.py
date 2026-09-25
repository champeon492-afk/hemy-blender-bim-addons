"""Native Blender/Bonsai saves, including a portable IFC snapshot inside .blend."""
import hashlib
from pathlib import Path
import bpy

from .core import NucleusError

SNAPSHOT = ".nucleus-embedded.ifc"
SNAPSHOT_HASH = "nucleus_ifc_sha256"


def bonsai():
    try:
        import bonsai.tool as tool
        if not tool.Blender.get_addon_preferences():
            raise ImportError("Bonsai is not enabled")
        return tool
    except (ImportError, AttributeError) as exc:
        raise NucleusError("Enable Bonsai before opening or saving IFC projects.") from exc


def has_ifc():
    try:
        return bool(bonsai().Ifc.get())
    except (NucleusError, RuntimeError):
        return False


def ifc_path():
    if not has_ifc():
        return ""
    return str(Path(bonsai().Ifc.get_path()).resolve()) if bonsai().Ifc.get_path() else ""


def execute_operator(operator, **kwargs):
    supported = operator.get_rna_type().properties.keys()
    result = operator("EXEC_DEFAULT", **{key: value for key, value in kwargs.items() if key in supported})
    if "FINISHED" not in result:
        raise NucleusError(f"Native file operation did not complete: {result}")


def save_ifc(path):
    path = Path(path).resolve()
    if not has_ifc():
        raise NucleusError("Create or open a Bonsai IFC project first.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tool = bonsai()
    prefs = tool.Blender.get_addon_preferences()
    # This connector saves a single native file. .blend saves embed their IFC snapshot.
    # Suppress automatic secondary .blend creation by Bonsai's metadata-save option.
    old_metadata = getattr(prefs, "save_metadata_blend_file", None)
    try:
        if old_metadata is not None:
            prefs.save_metadata_blend_file = False
        execute_operator(bpy.ops.bim.save_project, filepath=str(path), should_save_as=True,
                         use_relative_path=False, skip_recent=True)
    finally:
        if old_metadata is not None:
            prefs.save_metadata_blend_file = old_metadata
    if not path.is_file() or path.stat().st_size == 0:
        raise NucleusError("Bonsai did not produce the requested IFC file.")
    tool.Ifc.set_path(str(path))


def save_blend(path, pack_resources=True):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tool = bonsai() if has_ifc() else None
    original_path = tool.Ifc.get_path() if tool else None
    original_dirty = tool.Blender.get_bim_props().is_dirty if tool else None
    text = bpy.data.texts.get(SNAPSHOT)
    # Recreate our own snapshot only; refuse a name collision with an ordinary user text.
    if text and SNAPSHOT_HASH not in text:
        raise NucleusError(f"A text block named {SNAPSHOT} already exists. Rename it before saving.")
    try:
        if tool:
            snapshot_path = path.parent / "embedded.ifc"
            save_ifc(snapshot_path)
            content = snapshot_path.read_text(encoding="utf-8")
            if text is None:
                text = bpy.data.texts.new(SNAPSHOT)
            text.clear()
            text.write(content)
            text[SNAPSHOT_HASH] = hashlib.sha256(content.encode("utf-8")).hexdigest()
            text.use_fake_user = True
            # Store a relative sidecar path for users opening without this add-on.
            tool.Ifc.set_path(str(snapshot_path))
            tool.Blender.get_bim_props().ifc_file = "//embedded.ifc"
        elif text:
            bpy.data.texts.remove(text)
        if pack_resources:
            execute_operator(bpy.ops.file.pack_all)
        execute_operator(bpy.ops.wm.save_as_mainfile, filepath=str(path), check_existing=False,
                         relative_remap=True, copy=False)
    finally:
        if tool:
            tool.Ifc.set_path(original_path or "")
            tool.Blender.get_bim_props().is_dirty = original_dirty
    if not path.is_file():
        raise NucleusError("Blender did not produce the requested .blend file.")


def restore_embedded_ifc(cache):
    text = bpy.data.texts.get(SNAPSHOT)
    if not text or SNAPSHOT_HASH not in text:
        return False
    content = text.as_string()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if digest != text[SNAPSHOT_HASH]:
        raise NucleusError("The embedded IFC snapshot has changed; its integrity check failed.")
    # Never trust a filesystem path stored in the remote blend file.
    path = Path(cache) / "embedded" / digest / "embedded.ifc"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    tool = bonsai()
    from bonsai.bim.ifc import IfcStore
    IfcStore.purge()
    tool.Ifc.set_path(str(path))
    if not tool.Ifc.get():
        raise NucleusError("The embedded IFC snapshot could not be loaded.")
    return True


def open_native(path):
    path = Path(path).resolve()
    if path.suffix.lower() == ".blend":
        execute_operator(bpy.ops.wm.open_mainfile, filepath=str(path), load_ui=False, use_scripts=False)
    else:
        bonsai()
        execute_operator(bpy.ops.bim.load_project, filepath=str(path), is_advanced=False,
                         should_start_fresh_session=True, use_relative_path=False,
                         skip_autosave_recovery=True)
        if not has_ifc() or Path(ifc_path()) != path:
            raise NucleusError("Bonsai did not load the selected IFC project.")
