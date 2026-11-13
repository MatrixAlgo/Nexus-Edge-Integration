"""Tiny JSON HTTP framework on http.server (no third-party packages)."""
import http.client, json, logging, mimetypes, os, re, socket, ssl, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("web")


class HttpError(Exception):
    def __init__(self, status, message, detail=None):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


class Request:
    def __init__(self, handler, params):
        self.h = handler
        self.method = handler.command
        u = urllib.parse.urlsplit(handler.path)
        self.path = u.path
        self.query = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        self.params = params
        self.headers = handler.headers
        n = int(handler.headers.get("Content-Length") or 0)
        self.raw = handler.rfile.read(n) if n else b""

    def json(self):
        if not self.raw:
            return {}
        try:
            return json.loads(self.raw.decode("utf-8"))
        except ValueError:
            raise HttpError(400, "body is not valid JSON")


class Response:
    def __init__(self, status=200, body=b"", ctype="application/json", headers=None):
        self.status, self.body, self.ctype, self.headers = status, body, ctype, headers or {}


class App:
    def __init__(self, name, static_dir=None):
        self.name, self.static_dir, self.routes = name, static_dir, []

    def route(self, method, pattern):
        rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

        def deco(fn):
            self.routes.append((method, rx, fn))
            return fn
        return deco

    def dispatch(self, h):
        path = urllib.parse.urlsplit(h.path).path
        for method, rx, fn in self.routes:
            m = rx.match(path)
            if m and method == h.command:
                req = Request(h, m.groupdict())
                try:
                    out = fn(req)
                except HttpError as e:
                    return Response(e.status, json.dumps({"error": e.message, "detail": e.detail}).encode())
                except Exception as e:  # noqa
                    log.error("unhandled error", extra={"ctx": {"path": path, "exc": traceback.format_exc()}})
                    return Response(500, json.dumps({"error": "internal error", "detail": str(e)}).encode())
                if isinstance(out, Response):
                    return out
                status, body = (out if isinstance(out, tuple) else (200, out))
                return Response(status, json.dumps(body, default=str).encode())
        if h.command == "GET" and self.static_dir:
            return self.static(path)
        if any(rx.match(path) for _, rx, _ in self.routes):
            return Response(405, b'{"error":"method not allowed"}')
        return Response(404, b'{"error":"not found"}')

    def static(self, path):
        rel = path.lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(self.static_dir, rel))
        if not full.startswith(os.path.realpath(self.static_dir)) or not os.path.isfile(full):
            full = os.path.join(self.static_dir, "index.html")
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        return Response(200, data, ctype, {"Cache-Control": "no-cache"})

    def serve(self, host, port):
        app = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "D5/" + app.name

            def _go(self):
                r = app.dispatch(self)
                self.send_response(r.status)
                self.send_header("Content-Type", r.ctype)
                self.send_header("Content-Length", str(len(r.body)))
                for k, v in r.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(r.body)

            do_GET = do_POST = do_PUT = do_DELETE = _go

            def log_message(self, fmt, *args):
                pass

        srv = ThreadingHTTPServer((host, port), H)
        srv.daemon_threads = True
        log.info("listening", extra={"ctx": {"service": self.name, "host": host, "port": port}})
        srv.serve_forever()


class TransportError(Exception):
    """Network-level failure (refused, reset, timeout) - the request may or may not have reached the peer."""


def call(method, url, body=None, timeout=10, headers=None):
    """HTTP call returning (status, parsed_json_or_text, raw_bytes). Raises TransportError on network failure."""
    u = urllib.parse.urlsplit(url)
    data = None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    if u.scheme == "https":
        # only used for the local ACE admin REST API (self-signed certificate on 127.0.0.1)
        if u.hostname not in ("127.0.0.1", "localhost"):
            raise TransportError("https is only permitted to localhost")
        conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout,
                                           context=ssl._create_unverified_context())
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        conn.request(method, (u.path or "/") + ("?" + u.query if u.query else ""), body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
    except (OSError, http.client.HTTPException, socket.timeout) as e:
        raise TransportError("%s: %s" % (type(e).__name__, e))
    finally:
        conn.close()
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        parsed = raw.decode("utf-8", "replace")
    return resp.status, parsed, raw
