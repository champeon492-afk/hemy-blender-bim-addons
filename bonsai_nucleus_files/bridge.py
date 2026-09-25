"""Launch SDK workers without threads, listening ports, or shell commands."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .core import NucleusError, write_json


@dataclass
class Settings:
    python: str
    sdk: str
    cache: str
    timeout: float = 180.0


def discover():
    python = os.environ.get("BONSAI_NUCLEUS_PYTHON", "")
    sdk = os.environ.get("BONSAI_NUCLEUS_SDK", "")
    return python, sdk


class Job:
    def __init__(self, settings: Settings, payload):
        if not settings.python or not Path(settings.python).is_file():
            raise NucleusError("Set the SDK Python executable in the add-on preferences, or click Detect Runtime.")
        if not settings.sdk or not Path(settings.sdk).is_dir():
            raise NucleusError("Set the NVIDIA SDK folder in the add-on preferences.")
        self.directory = Path(settings.cache) / "jobs" / uuid.uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)
        self.response = self.directory / "response.json"
        request = self.directory / "request.json"
        self.payload = dict(payload, sdk=settings.sdk)
        write_json(request, self.payload)
        self.log = (self.directory / "worker.log").open("wb")
        command = [settings.python, str(Path(__file__).with_name("worker.py")),
                   "--request", str(request), "--response", str(self.response)]
        env = os.environ.copy()
        # Do not pass Blender-specific Python search paths into another interpreter.
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONUNBUFFERED"] = "1"
        self.started = time.monotonic()
        self.timeout = settings.timeout
        self.closed = False
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=self.log,
                stderr=self.log, env=env, cwd=str(self.directory),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except Exception:
            self.log.close()
            raise

    def poll(self):
        if self.closed:
            return None
        code = self.process.poll()
        if code is not None:
            self.closed = True
            self.log.close()
            if self.response.exists():
                return json.loads(self.response.read_text(encoding="utf-8"))
            return {"ok": False, "error": f"SDK worker exited ({code}). Details: {self.directory / 'worker.log'}"}
        if time.monotonic() - self.started > self.timeout:
            self.cancel()
            if self.response.exists():
                return json.loads(self.response.read_text(encoding="utf-8"))
            note = " Upload outcome is unknown; keep the recovery file and refresh before retrying." if self.payload["op"] == "upload" else ""
            return {"ok": False, "error": "Nucleus operation timed out. Check server access and complete browser sign-in." + note}
        return None

    def cancel(self):
        if self.closed:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.log.close()
        self.closed = True


def run_sync(settings, payload):
    """Command-line/test entry point; the Blender UI uses Job.poll via app timers."""
    job = Job(settings, payload)
    try:
        while True:
            result = job.poll()
            if result is not None:
                return result
            time.sleep(0.1)
    finally:
        job.cancel()
