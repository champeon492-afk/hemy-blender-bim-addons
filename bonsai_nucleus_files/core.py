"""URL and revision rules shared by the add-on and the isolated SDK worker."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

DEFAULT_FOLDER = ""
EXTENSIONS = {".ifc", ".ifczip", ".blend"}


class NucleusError(RuntimeError):
    pass


class ConflictError(NucleusError):
    pass


def normalize_url(value: str, folder: bool = False) -> str:
    value = value.strip().replace("\\_", "_").replace("\\", "/")
    parts = urlsplit(value)
    if parts.scheme.lower() != "omniverse" or not parts.hostname:
        raise NucleusError("Enter an omniverse://server/folder URL.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise NucleusError("Use a plain Nucleus URL without credentials, query or fragment.")
    if any(c.isspace() for c in parts.netloc):
        raise NucleusError("The server name cannot contain spaces.")
    segments = []
    for raw in parts.path.split("/"):
        if not raw:
            continue
        segment = unquote(raw, errors="strict")
        if segment in (".", "..") or any(c in segment for c in "/\\\0"):
            raise NucleusError("Relative paths and encoded separators are not supported.")
        if any(ord(c) < 32 for c in segment):
            raise NucleusError("The URL contains control characters.")
        segments.append(quote(segment, safe="-._~"))
    path = "/" + "/".join(segments)
    if folder and not path.endswith("/"):
        path += "/"
    return urlunsplit(("omniverse", parts.netloc.lower(), path, "", ""))


def child_url(folder: str, name: str) -> str:
    if not name or name in (".", "..") or any(c in name for c in "/\\\0"):
        raise NucleusError("Select a file or enter a single filename.")
    return normalize_url(normalize_url(folder, True) + quote(name, safe="-._~"))


def parent_url(url: str) -> str:
    p = urlsplit(normalize_url(url))
    path = p.path.rstrip("/").rsplit("/", 1)[0] + "/"
    return urlunsplit((p.scheme, p.netloc, path, "", ""))


def filename(url: str) -> str:
    return unquote(urlsplit(normalize_url(url)).path.rsplit("/", 1)[-1])


def extension(url: str) -> str:
    return Path(filename(url)).suffix.lower()


def validate_native(url: str) -> str:
    url = normalize_url(url)
    if extension(url) not in EXTENSIONS:
        raise NucleusError("Supported native files: .ifc, .ifczip and .blend.")
    return url


def local_name(url: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename(url)).strip(" .")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
        name = "_" + name
    suffix = Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    return stem[:160] + suffix.lower()


def revision(entry) -> dict:
    return {"version": str(getattr(entry, "version", "") or ""),
            "hash": str(getattr(entry, "hash", "") or ""),
            "modified": str(getattr(entry, "modified_time", "") or ""),
            "size": int(getattr(entry, "size", 0))}


def same_revision(left: dict | None, right: dict | None) -> bool:
    if left is None or right is None:
        return left is right
    # Compare all supplied server evidence; a version can be reused after recreation.
    return left == right and bool(left.get("version") or left.get("hash") or left.get("modified"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)
