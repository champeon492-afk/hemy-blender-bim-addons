"""Loopback-only HTTP bridge. Background threads never access USD or Blender."""
import hmac
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .protocol import MAX_BYTES, validate


class Pending:
    def __init__(self, packet):
        self.packet = packet
        self.done = threading.Event()
        self.result = None
        self.status = 200
        self.cancelled = False

    def finish(self, result, status=200):
        self.result, self.status = result, status
        self.done.set()


class BridgeServer:
    def __init__(self, port, token, target, epoch):
        if len(token) < 24:
            raise ValueError('Use a receiver token of at least 24 characters.')
        self.pending = queue.Queue(maxsize=2)
        self.target, self.epoch, self.token = target, epoch, token
        self.closed = False
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def reply(self, status, body):
                data = json.dumps(body).encode('utf-8')
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (OSError, BrokenPipeError):
                    pass

            def authenticated(self):
                # No CORS, and reject browser-origin requests as defense in depth.
                auth = self.headers.get('Authorization', '')
                if self.headers.get('Origin') or not hmac.compare_digest(
                        auth.encode(), ('Bearer ' + bridge.token).encode()):
                    self.reply(401, {'error': 'Invalid bridge token.'})
                    return False
                if bridge.closed:
                    self.reply(503, {'error': 'Receiver stopped.'})
                    return False
                return True

            def do_GET(self):
                if not self.authenticated():
                    return
                if self.path != '/health':
                    self.reply(404, {'error': 'Unknown endpoint.'})
                    return
                self.reply(200, {'version': 1, 'stage': bridge.target, 'epoch': bridge.epoch})

            def do_POST(self):
                if not self.authenticated():
                    return
                if self.path != '/update':
                    self.reply(404, {'error': 'Unknown endpoint.'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if self.headers.get('Transfer-Encoding') or not 0 < length <= MAX_BYTES:
                        self.reply(413, {'error': 'Update exceeds 64 MiB or has invalid length.'})
                        return
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError('Incomplete request body.')
                    packet = validate(json.loads(raw))
                    if packet['stage'] != bridge.target:
                        self.reply(409, {'error': 'Blender and receiver stage URLs differ.'})
                        return
                    item = Pending(packet)
                    bridge.pending.put_nowait(item)
                except queue.Full:
                    self.reply(503, {'error': 'Receiver busy; retry.'})
                    return
                except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError) as exc:
                    self.reply(400, {'error': str(exc)})
                    return
                if not item.done.wait(20):
                    item.cancelled = True
                    self.reply(504, {'error': 'Kit did not apply the update in time; reconnect.'})
                    return
                self.reply(item.status, item.result)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False

        self.http = Server(('127.0.0.1', port), Handler)
        self.port = self.http.server_port
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={'poll_interval': 0.1},
                                       daemon=True, name='BonsaiSyncHTTP')
        self.thread.start()

    def close(self):
        self.closed = True
        while True:
            try:
                self.pending.get_nowait().finish({'error': 'Receiver stopped.'}, 503)
            except queue.Empty:
                break
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=1)
