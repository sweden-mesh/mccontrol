"""The HTTP side: a stdlib ThreadingHTTPServer on 127.0.0.1, a route table, JSON in and out.

Handlers are plain functions `f(ctx, params, query, body) -> (status, payload)`; this module
only parses, dispatches and turns exceptions into the one error shape. Nothing here imports
curses, and nothing here knows what a device is.
"""

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import store as _store, transport
from . import errors, jobs, routes

MAX_BODY = 1 << 20


class Locked:
    """The store with one lock around every call: the jobs' threads and the requests'
    threads read-modify-write the same JSON files. A call is milliseconds; the long waits
    are in the transport, outside it."""

    def __init__(self, store):
        self._store = store
        self._lock = threading.RLock()

    def __getattr__(self, name):
        value = getattr(self._store, name)
        if not callable(value):
            return value

        def call(*a, **k):
            with self._lock:
                return value(*a, **k)
        return call


class Context:
    def __init__(self, store):
        self.store = Locked(store)
        self.tags = set()               # node ids; the interfaces' shared tag state
        self.jobs = jobs.Registry()


def _compile(template):
    pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template)
    return re.compile("^" + pattern + "$")


ROUTES = [(method, template, _compile(template), func) for method, template, func in routes.TABLE + jobs.TABLE]


def match(method, path):
    """`(template, func, params)` for a request; raises 404 or 405. Literal routes are listed
    before parameterised ones, so `/devices/adopt` is never a device named adopt."""
    allowed = []
    for m, template, rx, func in ROUTES:
        hit = rx.match(path)
        if not hit:
            continue
        if m == method:
            return template, func, {k: urllib.parse.unquote(v) for k, v in hit.groupdict().items()}
        allowed.append(m)
    if allowed:
        raise errors.ApiError(405, "method_not_allowed", f"{method} {path}: allowed {', '.join(sorted(set(allowed)))}")
    raise errors.not_found(f"no route {path}")


class Handler(BaseHTTPRequestHandler):
    server_version = "mccontrol"

    def log_message(self, *_a):
        pass                            # a screen owns the terminal

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query, keep_blank_values=True).items()}
        try:
            _t, func, params = match(method, url.path)
            status, payload = func(self.server.ctx, params, query, self._body())
        except Exception as exc:        # every failure leaves as the one error shape
            status, code, message = errors.classify(exc)
            payload = errors.body(code, message)
        if isinstance(payload, routes.Html):
            self._send(status, payload.text.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > MAX_BODY:
            raise errors.bad("the body is too large")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise errors.bad("the body is not JSON") from None
        if not isinstance(data, dict):
            raise errors.bad("the body is a JSON object")
        return data

    def _send(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(store=None, transport=None, port=0):
    """Start the API on 127.0.0.1 (a free port unless given) in a daemon thread, over
    `store`, or with none the one in MCCONTROL_HOME (or the default directory).

    `transport`, for tests: a scripted `{node or "node|command": [code, reply]}` object that
    stands in for the broker, as MCCONTROL_FAKE_RESULTS does. Returns `(server, base_url)`."""
    if transport is not None:
        _transport().FAKE_SCRIPT = transport
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.ctx = Context(store if store is not None else _store.Store())
    server.scripted = transport is not None
    # a short poll: stop() waits for it, and a command line starts and stops a server per command
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, name="mccontrol-api", daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def stop(server):
    """Cancel what is running, stop serving, release the port."""
    server.ctx.jobs.cancel_all()
    server.shutdown()
    server.server_close()
    if server.scripted:
        _transport().FAKE_SCRIPT = None


def _transport():
    return transport
