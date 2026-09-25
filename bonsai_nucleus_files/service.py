"""Nucleus file operations. No Blender imports and no direct HTTP emulation."""
from pathlib import Path
import uuid

try:
    from .core import (NucleusError, ConflictError, normalize_url, validate_native,
                       revision, same_revision, sha256, child_url)
except ImportError:  # worker.py runs as a standalone script.
    from core import (NucleusError, ConflictError, normalize_url, validate_native,
                      revision, same_revision, sha256, child_url)


class NucleusService:
    def __init__(self, client):
        self.client = client

    def check(self, result, action):
        if result != self.client.Result.OK:
            raise NucleusError(f"{action}: {result}. Check Nucleus login, permissions and connectivity.")

    def stat(self, url):
        result, entry = self.client.stat(normalize_url(url))
        if result == self.client.Result.ERROR_NOT_FOUND:
            return None
        self.check(result, "Read file details")
        return entry

    def list(self, url):
        url = normalize_url(url, True)
        result, entries = self.client.list(url)
        self.check(result, "Browse folder")
        items = []
        for e in entries:
            folder = bool(e.flags & self.client.ItemFlags.CAN_HAVE_CHILDREN)
            # The SDK returns literal child names, not URL-encoded paths.
            try:
                child = child_url(url, e.relative_path)
            except NucleusError:
                continue
            items.append({"name": e.relative_path, "url": child, "folder": folder,
                          "size": 0 if folder else int(e.size),
                          "write": bool(e.access & self.client.AccessFlags.WRITE),
                          "revision": None if folder else revision(e)})
        return {"url": url, "items": sorted(items, key=lambda x: (not x["folder"], x["name"].casefold()))}

    def download(self, url, local):
        url = validate_native(url)
        local = Path(local).resolve()
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            raise NucleusError("Download destination already exists; create a fresh working copy.")
        before = self.stat(url)
        if before is None:
            raise NucleusError("The selected file no longer exists.")
        temp = local.with_name(local.name + "." + uuid.uuid4().hex + ".part")
        try:
            self.check(self.client.copy(url, str(temp), self.client.CopyBehavior.ERROR_IF_EXISTS), "Download")
            after = self.stat(url)
            if after is None or not same_revision(revision(before), revision(after)):
                raise ConflictError("The remote file changed while downloading. Refresh and open it again.")
            if not temp.is_file() or temp.stat().st_size != int(after.size):
                raise NucleusError("The downloaded file is incomplete.")
            digest = sha256(temp)
            temp.replace(local)
            return {"url": url, "local": str(local), "revision": revision(after), "sha256": digest}
        finally:
            temp.unlink(missing_ok=True)

    def upload(self, url, local, expected, message="Saved from Bonsai Nucleus Files"):
        url = validate_native(url)
        local = Path(local).resolve()
        if not local.is_file() or local.stat().st_size == 0:
            raise NucleusError("The local native file is missing or empty. Nothing was uploaded.")
        existing = self.stat(url)
        actual = revision(existing) if existing is not None else None
        if not same_revision(expected, actual):
            raise ConflictError("The Nucleus file changed or the name is already in use. Use Save As with a new name, or reopen the latest version.")
        locked = False
        uploaded = False
        warning = ""
        try:
            if existing is not None:
                # Hold a server-enforced lock across the second revision check and upload.
                self.check(self.client.lock(url), "Lock file for saving")
                locked = True
                current = self.stat(url)
                if current is None or not same_revision(expected, revision(current)):
                    raise ConflictError("The remote file changed before the save lock was acquired.")
            behavior = self.client.CopyBehavior.OVERWRITE if existing is not None else self.client.CopyBehavior.ERROR_IF_EXISTS
            self.check(self.client.copy(str(local), url, behavior, message), "Upload")
            uploaded = True
            current = self.stat(url)
            if current is None or int(current.size) != local.stat().st_size:
                raise NucleusError("Upload finished but its remote size could not be verified.")
            outcome = {"url": url, "local": str(local), "revision": revision(current), "sha256": sha256(local)}
        except Exception as exc:
            if uploaded:
                raise NucleusError(f"Upload may have completed; do not overwrite blindly. {exc}") from exc
            raise
        finally:
            if locked:
                result = self.client.unlock(url)
                if result != self.client.Result.OK:
                    warning = f"Saved, but unlocking returned {result}; reconnect to release this client lock."
        outcome["warning"] = warning
        return outcome
