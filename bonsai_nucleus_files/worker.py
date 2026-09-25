"""One request per SDK process, isolated from Blender's Python ABI and UI thread."""
import argparse
import json
import os
from pathlib import Path
import sys

from core import NucleusError, ConflictError, write_json
from service import NucleusService


def load_client(root):
    root = Path(root).expanduser().resolve()
    candidates = [root, root / "release/bindings-python", root / "bindings-python",
                  root / "extscore/omni.client.lib", root / "kit/extscore/omni.client.lib"]
    binding = next((p for p in candidates if (p / "omni/client/__init__.py").is_file()), None)
    if binding is None:
        raise NucleusError("NVIDIA client bindings not found. Set SDK folder to omni.client.lib or the extracted Client Library package.")
    sys.path.insert(0, str(binding))
    dll_dirs = [binding / "bin", binding, binding.parent, root / "release", root / "bin"]
    handles = []
    for path in dll_dirs:
        if path.is_dir():
            if hasattr(os, "add_dll_directory"):
                handles.append(os.add_dll_directory(str(path)))
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")
    try:
        import omni.client as client
    except ImportError as exc:
        raise NucleusError(f"Cannot load omni.client with Python {sys.version.split()[0]}. Choose an SDK-compatible Python executable (usually 3.10–3.12). {exc}") from exc
    # Retain DLL directory handles for the full lifetime of the client.
    client._bonsai_dll_handles = handles
    client.set_log_level(client.LogLevel.ERROR)
    client.set_product_info("Bonsai Nucleus Files", "1.0.0")
    if not client.initialize():
        raise NucleusError("NVIDIA Client Library initialization failed.")
    client.bypass_list_cache(True)
    return client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()
    client = None
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        client = load_client(request["sdk"])
        service = NucleusService(client)
        op = request["op"]
        if op == "list":
            data = service.list(request["url"])
        elif op == "stat":
            from core import revision
            entry = service.stat(request["url"])
            data = {"revision": revision(entry) if entry is not None else None}
        elif op == "download":
            data = service.download(request["url"], request["local"])
        elif op == "upload":
            data = service.upload(request["url"], request["local"], request.get("expected"), request.get("message", "Saved from Bonsai Nucleus Files"))
        else:
            raise NucleusError("Unknown operation.")
        response = {"ok": True, "data": data, "client_version": client.get_version()}
    except Exception as exc:
        response = {"ok": False, "error": str(exc), "conflict": isinstance(exc, ConflictError)}
    # Persist the result before shutdown, which can take time on a disconnected server.
    write_json(Path(args.response), response)
    if client is not None:
        client.shutdown()
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
