"""D5 Lightweight Edge Sync Agent (one process per vessel) - Vessel AMOS local API + outbox + dispatcher.

    python3 edge-runtime/agent/main.py vessel-simulator/vessel-01/edge.json
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, HERE]

from common.util import setup_logging  # noqa: E402
from common.web import App, HttpError  # noqa: E402
from dispatcher import Dispatcher, HttpTransport  # noqa: E402
from management import Management  # noqa: E402
from metrics import Metrics  # noqa: E402
from store import EdgeStore  # noqa: E402


def build(cfg_path):
    cfg = json.load(open(cfg_path))
    cfg["dataDirAbs"] = os.path.join(ROOT, cfg["dataDir"])
    os.makedirs(cfg["dataDirAbs"], exist_ok=True)
    setup_logging("edge-" + cfg["vesselId"].lower())
    store = EdgeStore(os.path.join(cfg["dataDirAbs"], "edge.db"), cfg["vesselId"], cfg.get("initialAgentVersion", "1.0.0"))
    disp = Dispatcher(store, HttpTransport(cfg["satelliteLinkUrl"], cfg["eventsPath"], cfg["statusPath"]))
    metrics = Metrics(cfg["dataDirAbs"], cfg.get("resourceTargets"))
    mgmt = Management(store, disp, cfg, metrics)
    app = App("edge-" + cfg["vesselId"])

    def actor(req):
        return req.headers.get("X-D5-User") or "chief-engineer@" + cfg["vesselId"].lower()

    @app.route("GET", "/health")
    def health(req):
        return {"vesselId": cfg["vesselId"], "status": "UP", "dispatcher": disp.is_alive(), "management": mgmt.is_alive()}

    @app.route("GET", "/api/status")
    def status(req):
        av = store.db.q1("SELECT version, previous_version, manifest FROM edge_versions WHERE active=1") or {}
        return {"vesselId": cfg["vesselId"], "agentVersion": av.get("version"), "previousAgentVersion": av.get("previous_version"),
                "agentFeatures": (json.loads(av["manifest"]) if av.get("manifest") else {}).get("features"),
                "configVersion": store.config_version(), "config": store.config(),
                "linkObserved": store.get_state("link_observed", "UP"), "offlineSince": store.get_state("offline_since"),
                "lastSyncAt": store.get_state("last_sync_at"), "queue": store.queue_counts(),
                "records": store.db.scalar("SELECT COUNT(*) FROM operational_records"),
                "threads": {"dispatcher": disp.is_alive(), "management": mgmt.is_alive()},
                "resources": metrics.snapshot()}

    @app.route("GET", "/api/records")
    def records(req):
        return store.records(req.query.get("type"))

    @app.route("GET", "/api/records/{rid}")
    def record(req):
        r = store.record(req.params["rid"])
        if not r:
            raise HttpError(404, "record not found")
        return r

    @app.route("POST", "/api/records")
    def create(req):
        st, body = store.create_record(req.json(), actor(req))
        disp.wake.set()
        return st, body

    @app.route("PUT", "/api/records/{rid}")
    def update(req):
        st, body = store.update_record(req.params["rid"], req.json(), actor(req))
        disp.wake.set()
        return st, body

    @app.route("GET", "/api/events")
    def events(req):
        return store.events(req.query.get("status"), int(req.query.get("limit", "300")))

    @app.route("GET", "/api/events/{eid}")
    def event(req):
        r = store.event_detail(req.params["eid"])
        if not r:
            raise HttpError(404, "event not found")
        return r

    @app.route("POST", "/api/events/{eid}/replay")
    def replay(req):
        st, body = store.replay(req.params["eid"])
        disp.wake.set()
        return st, body

    @app.route("GET", "/api/sync")
    def sync(req):
        return store.sync_view()

    @app.route("GET", "/api/config")
    def config(req):
        return {"active": store.config(), "activeVersion": store.config_version(),
                "history": store.db.q("SELECT version, received_at, applied_at, status, active, detail FROM edge_configuration ORDER BY version DESC")}

    @app.route("GET", "/api/versions")
    def versions(req):
        return store.db.q("SELECT version, sha256, staged_path, staged_at, activated_at, status, active, previous_version, detail "
                          "FROM edge_versions ORDER BY COALESCE(activated_at, staged_at) DESC")

    @app.route("POST", "/api/demo/generate")
    def generate(req):
        b = req.json()
        n = max(1, min(int(b.get("count", 10)), 200))
        out = store.generate(n, actor(req), b.get("priorities"))
        disp.wake.set()
        return 201, out

    return cfg, app, disp, mgmt


if __name__ == "__main__":
    cfg, app, disp, mgmt = build(sys.argv[1])
    disp.start()
    mgmt.start()
    app.serve(cfg["host"], int(cfg["port"]))
