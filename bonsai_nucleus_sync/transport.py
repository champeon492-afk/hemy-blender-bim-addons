"""One in-flight update; acknowledges only after the Kit main thread applies USD."""
import json
import queue
import threading
import urllib.error
import urllib.request
from .core.protocol import MAX_BYTES


class Transport:
    def __init__(self, port, token):
        self.url = 'http://127.0.0.1:' + str(port)
        self.token = token
        self.tasks = queue.Queue(maxsize=1)
        self.results = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='BonsaiSyncSender')
        self.thread.start()

    def submit(self, packet):
        self.tasks.put_nowait(packet)

    def _run(self):
        # Ignore operating-system proxies for loopback traffic.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not self.stop_event.is_set():
            try:
                packet = self.tasks.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                body = json.dumps(packet, separators=(',', ':'), allow_nan=False).encode('utf-8')
                if len(body) > MAX_BYTES:
                    raise ValueError('Scene update exceeds 64 MiB. Use a smaller Sync collection.')
                request = urllib.request.Request(self.url + '/update', data=body,
                    headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
                with opener.open(request, timeout=25) as response:
                    result = json.loads(response.read(65536))
                if result.get('seq') != packet['seq']:
                    raise ValueError('Invalid acknowledgement from receiver.')
                self.results.put((True, result))
            except urllib.error.HTTPError as exc:
                try:
                    error = json.loads(exc.read(65536)).get('error', str(exc))
                except (ValueError, OSError):
                    error = str(exc)
                self.results.put((False, error))
            except Exception as exc:
                self.results.put((False, str(exc)))

    def close(self):
        self.stop_event.set()
