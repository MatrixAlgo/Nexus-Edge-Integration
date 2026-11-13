"""Satellite link simulator - one HTTP relay per vessel between the edge agent and the shore.

    python3 network-simulator/netsim.py [network-simulator/netsim.json]

Every byte the edge sends to shore (business events to IBM ACE, heartbeats/config/versions/downlink to the
central management plane) goes through the vessel's relay port. When a link is DISCONNECTED the relay closes
the TCP connection without forwarding anything - the request genuinely does not reach ACE or central.
Controls (per vessel): connect/disconnect, latency (ms), jitter, bandwidth (kbit/s), packet-loss %,
and INTERMITTENT mode (automatic up/down cycling).
"""
import http.client, json, os, random, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from common.util import log, now_iso, setup_logging  # noqa: E402

CFG = json.load(open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "netsim.json")))
STATE_FILE = os.path.join(ROOT, CFG["stateFile"])
L = setup_logging("network-simulator")
LOCK = threading.Lock()
DEFAULT = {"state": "CONNECTED", "latencyMs": 400, "jitterMs": 80, "bandwidthKbps": 0, "lossPercent": 0,
           "intermittentUpSec": 15, "intermittentDownSec": 8}


def load_state():
    try:
        st = json.load(open(STATE_FILE))
    except (OSError, ValueError):
        st = {}
    for v in CFG["links"]:
        s = st.setdefault(v, dict(DEFAULT))
        for k, val in DEFAULT.items():
            s.setdefault(k, val)
        s.setdefault("changedAt", now_iso())
        s.setdefault("stats", {"forwarded": 0, "blocked": 0, "bytesUp": 0, "bytesDown": 0})
    return st


STATE = load_state()


def save():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(STATE, f, indent=1)
    os.replace(tmp, STATE_FILE)


def link_up(vid):
    s = STATE[vid]
    if s["state"] == "CONNECTED":
        return True
    if s["state"] == "DISCONNECTED":
        return False
    cycle = s["intermittentUpSec"] + s["intermittentDownSec"]   # INTERMITTENT
    return (time.time() % cycle) < s["intermittentUpSec"]


def public(vid):
    s = dict(STATE[vid])
    s["vesselId"] = vid
    s["effectiveUp"] = link_up(vid)
    s["relayPort"] = CFG["links"][vid]["relayPort"]
    return s


def make_relay(vid):
    spec = CFG["links"][vid]

    class Relay(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _drop(self, why):
            with LOCK:
                STATE[vid]["stats"]["blocked"] += 1
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def _relay(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            s = STATE[vid]
            if not link_up(vid):
                return self._drop("link down")
            if s["lossPercent"] and random.uniform(0, 100) < s["lossPercent"]:
                return self._drop("simulated loss")
            delay = max(0, s["latencyMs"] + random.uniform(-s["jitterMs"], s["jitterMs"])) / 1000.0
            if s["bandwidthKbps"]:
                delay += ((n + 600) * 8) / (s["bandwidthKbps"] * 1000.0)
            time.sleep(delay)
            if not link_up(vid):   # link dropped while the request was "in the air"
                return self._drop("link down in transit")
            target = spec["routes"]["ace"] if self.path.startswith("/d5/") else spec["routes"]["central"]
            host, port = target.split(":")
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection", "content-length")}
            hdrs["X-D5-Via"] = "satellite-sim/" + vid
            try:
                c = http.client.HTTPConnection(host, int(port), timeout=30)
                c.request(self.command, self.path, body=body, headers=hdrs)
                r = c.getresponse()
                data = r.read()
                c.close()
            except (OSError, http.client.HTTPException) as e:
                self.send_response(502)
                msg = json.dumps({"error": "shore endpoint unreachable", "detail": str(e)}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            if s["bandwidthKbps"]:
                time.sleep((len(data) * 8) / (s["bandwidthKbps"] * 1000.0))
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            with LOCK:
                st = STATE[vid]["stats"]
                st["forwarded"] += 1
                st["bytesUp"] += n
                st["bytesDown"] += len(data)

        do_GET = do_POST = do_PUT = do_DELETE = _relay

    return Relay


class Control(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/links", "/links/"):
            return self._send(200, {v: public(v) for v in STATE})
        if self.path.startswith("/links/") and self.path[7:] in STATE:
            return self._send(200, public(self.path[7:]))
        if self.path == "/health":
            return self._send(200, {"status": "UP"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        vid = self.path[7:] if self.path.startswith("/links/") else None
        if vid not in STATE:
            return self._send(404, {"error": "unknown vessel link"})
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "invalid JSON"})
        with LOCK:
            s = STATE[vid]
            if "state" in b:
                if b["state"] not in ("CONNECTED", "DISCONNECTED", "INTERMITTENT"):
                    return self._send(400, {"error": "state must be CONNECTED, DISCONNECTED or INTERMITTENT"})
                if b["state"] != s["state"]:
                    s["state"], s["changedAt"] = b["state"], now_iso()
            for k, lo, hi in (("latencyMs", 0, 10000), ("jitterMs", 0, 5000), ("bandwidthKbps", 0, 100000),
                              ("lossPercent", 0, 100), ("intermittentUpSec", 1, 600), ("intermittentDownSec", 1, 600)):
                if k in b:
                    v = b[k]
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                        return self._send(400, {"error": "%s must be between %s and %s" % (k, lo, hi)})
                    s[k] = v
            save()
        log(L, "link changed", vesselId=vid, **{k: v for k, v in b.items()})
        self._send(200, public(vid))


def serve(port, handler):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()


if __name__ == "__main__":
    save()
    for vid, spec in CFG["links"].items():
        serve(spec["relayPort"], make_relay(vid))
        log(L, "relay listening", vesselId=vid, port=spec["relayPort"], routes=spec["routes"])
    log(L, "control API listening", port=CFG["controlPort"])
    srv = ThreadingHTTPServer(("127.0.0.1", CFG["controlPort"]), Control)
    srv.daemon_threads = True
    srv.serve_forever()
