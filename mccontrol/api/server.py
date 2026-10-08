"""The HTTP side: a stdlib ThreadingHTTPServer on 127.0.0.1, a route table, JSON in and out.

Handlers are plain functions `f(ctx, params, query, body) -> (status, payload)`; this module
only parses, dispatches and turns exceptions into the one error shape. Nothing here imports
curses, and nothing here knows what a device is.
"""

import hmac
import json
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    def __init__(self, store, script=None):
        self.store = Locked(store)
        self.script = script            # this server's stand-in broker, in tests; None in use
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
        path = self._inside(url.path)
        if path is None:                # not under the secret: nothing to say, not even why
            self._send(404, b"", "text/plain")
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query, keep_blank_values=True).items()}
        try:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in self.server.origins:
                raise errors.ApiError(403, "forbidden", "a request from another origin")
            _t, func, params = match(method, path)
            status, payload = func(self.server.ctx, params, query, self._body())
        except Exception as exc:        # every failure leaves as the one error shape
            status, code, message = errors.classify(exc)
            payload = errors.body(code, message)
        if isinstance(payload, routes.Html):
            self._send(status, payload.text.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _inside(self, path):
        """The path under `/<secret>`, or None. The secret is what keeps a web page or another
        local user's process from driving the signing key: it is only in the base URL handed
        to the caller of serve()."""
        prefix = "/" + self.server.secret
        head, rest = path[:len(prefix)], path[len(prefix):]
        if not hmac.compare_digest(head.encode(), prefix.encode()) or (rest and not rest.startswith("/")):
            return None
        return "/" + rest.lstrip("/")

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise errors.bad("Content-Length is not a number") from None
        if not length:
            return {}
        if length > MAX_BODY:
            raise errors.bad("the body is too large")
        media = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if media != "application/json":
            raise errors.ApiError(415, "unsupported_media_type", "a body is application/json")
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


def serve(store, transport=None, port=0):
    """Start the API on 127.0.0.1 (a free port unless given) in a daemon thread, under an
    unguessable path: `base_url` is `http://127.0.0.1:<port>/<secret>/`, and a request
    outside it, or carrying another page's Origin, is refused.

    `transport`, for tests: a scripted `{node or "node|command": [code, reply]}` object that
    stands in for the broker, as MCCONTROL_FAKE_RESULTS does. Returns `(server, base_url)`."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.ctx = Context(store, transport)
    server.secret = secrets.token_urlsafe(32)
    server.origin = f"http://127.0.0.1:{server.server_address[1]}"
    # the page /docs serves may be opened by either name for the loopback; nothing else
    server.origins = (server.origin, f"http://localhost:{server.server_address[1]}")
    threading.Thread(target=server.serve_forever, name="mccontrol-api", daemon=True).start()
    return server, f"{server.origin}/{server.secret}/"


def stop(server):
    """Cancel what is running, stop serving, release the port."""
    server.ctx.jobs.cancel_all()
    server.shutdown()
    server.server_close()
