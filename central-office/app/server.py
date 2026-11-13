"""D5 Central Office service: Office AMOS + central processing (called by IBM ACE) + fleet management plane
+ the management console API and static dashboard.

    python3 central-office/app/server.py [central-office/config/central.json]
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, HERE, os.path.join(ROOT, "dashboard", "backend")]

from common.db import Database  # noqa: E402
from common.util import setup_logging, log  # noqa: E402
from common.web import App, HttpError  # noqa: E402
import fleet, processing  # noqa: E402
import console  # noqa: E402

cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "central-office", "config", "central.json")
CFG = json.load(open(cfg_path))
L = setup_logging("central-office")
with open(os.path.join(ROOT, "central-office", "database", "schema.sql")) as f:
    DB = Database(os.path.join(ROOT, CFG["database"]), f.read())
POLICIES = {k: v for k, v in json.load(open(os.path.join(ROOT, CFG["conflictPolicy"]))).items() if not k.startswith("_")}
fleet.seed(DB, CFG)
CONSOLE = console.Console(DB, CFG)
app = App("central-office", static_dir=os.path.join(ROOT, "dashboard", "frontend"))


def actor(req):
    return req.headers.get("X-D5-User") or "office-admin"


# ---------------------------------------------------------------- internal API, called by IBM ACE flows only
@app.route("POST", "/internal/process-event")
def process_event(req):
    mq = {"queue": req.headers.get("X-D5-MQ-Queue"), "backoutCount": int(req.headers.get("X-D5-MQ-Backout") or 0)}
    evt = req.json()
    status, body = processing.process_event(DB, POLICIES, evt, mq)
    log(L, "event processed", eventId=body.get("eventId"), result=body.get("result"), outcome=body.get("outcome"),
        queue=mq["queue"], backout=mq["backoutCount"], http=status)
    return status, body


@app.route("POST", "/internal/ack")
def ack(req):
    status, body = processing.record_ack(DB, req.json())
    log(L, "business ack recorded", eventId=body.get("eventId"), http=status)
    return status, body


@app.route("GET", "/internal/event-status")
def event_status(req):
    eid = req.query.get("eventId", "")
    if not processing.UUID_RX.match(eid):
        raise HttpError(400, "eventId must be a UUID")
    return processing.event_status(DB, eid)


# ---------------------------------------------------------------- management plane, reached by edges over the satellite link
@app.route("POST", "/mgmt/vessels/{v}/heartbeat")
def hb(req):
    return fleet.heartbeat(DB, req.params["v"], req.json())


@app.route("GET", "/mgmt/vessels/{v}/commands")
def commands(req):
    return fleet.pending_commands(DB, req.params["v"])


@app.route("POST", "/mgmt/vessels/{v}/commands/{kind}/{id}")
def command_result(req):
    return fleet.report_command(DB, req.params["v"], req.params["kind"], int(req.params["id"]), req.json())


@app.route("GET", "/mgmt/packages/{version}")
def package(req):
    return fleet.get_package(DB, req.params["version"])


@app.route("GET", "/mgmt/vessels/{v}/downlink")
def downlink(req):
    return fleet.downlink(DB, req.params["v"], int(req.query.get("after", "0")))


# ---------------------------------------------------------------- console API (dashboard)
@app.route("GET", "/api/overview")
def overview(req):
    return CONSOLE.overview()


@app.route("GET", "/api/vessels")
def vessels(req):
    return CONSOLE.vessels()


@app.route("GET", "/api/events")
def events(req):
    return CONSOLE.events(req.query.get("vessel"), req.query.get("status"), int(req.query.get("limit", "300")))


@app.route("GET", "/api/sync")
def sync(req):
    return CONSOLE.sync()


@app.route("GET", "/api/central/records")
def central_records(req):
    return CONSOLE.central_records(req.query.get("vessel"))


@app.route("GET", "/api/central/records/{rid}")
def central_record(req):
    return CONSOLE.central_record(req.params["rid"])


@app.route("GET", "/api/central/ledger")
def ledger(req):
    return CONSOLE.ledger(int(req.query.get("limit", "300")))


@app.route("GET", "/api/central/duplicates")
def duplicates(req):
    return DB.q("SELECT * FROM duplicate_log ORDER BY id DESC LIMIT 200")


@app.route("POST", "/api/office/records")
def office_upsert(req):
    return processing.office_upsert(DB, req.json(), actor(req))


@app.route("GET", "/api/conflicts")
def conflicts(req):
    return CONSOLE.conflicts()


@app.route("POST", "/api/conflicts/{cid}/resolve")
def resolve(req):
    b = req.json()
    return processing.resolve_conflict(DB, req.params["cid"], b.get("decisions") or {}, b.get("actor") or actor(req),
                                       b.get("note"))


@app.route("GET", "/api/config")
def config(req):
    return CONSOLE.config_state()


@app.route("POST", "/api/config/publish")
def config_publish(req):
    return fleet.publish_config(DB, req.json(), actor(req))


@app.route("GET", "/api/versions")
def versions(req):
    return CONSOLE.version_state()


@app.route("POST", "/api/versions/publish")
def version_publish(req):
    return fleet.publish_version(DB, req.json(), actor(req))


@app.route("POST", "/api/versions/rollback")
def version_rollback(req):
    return fleet.request_rollback(DB, req.json(), actor(req))


@app.route("GET", "/api/audit")
def audit(req):
    args, where = [], []
    for k, col in (("entity", "entity"), ("id", "entity_id"), ("action", "action")):
        if req.query.get(k):
            where.append("%s=?" % col)
            args.append(req.query[k])
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    return DB.q(sql, args + [int(req.query.get("limit", "300"))])


@app.route("GET", "/api/platform")
def platform(req):
    return CONSOLE.platform()


@app.route("GET", "/api/network")
def network(req):
    return CONSOLE.network_get()


@app.route("POST", "/api/network/{v}")
def network_set(req):
    return CONSOLE.network_set(req.params["v"], req.json(), actor(req))


@app.route("GET", "/api/vessel/{v}/(?P<rest>.+)")
def vessel_get(req):
    return CONSOLE.vessel_proxy("GET", req.params["v"], req.params["rest"], req.query, None)


@app.route("POST", "/api/vessel/{v}/(?P<rest>.+)")
def vessel_post(req):
    return CONSOLE.vessel_proxy("POST", req.params["v"], req.params["rest"], req.query, req.json())


@app.route("PUT", "/api/vessel/{v}/(?P<rest>.+)")
def vessel_put(req):
    return CONSOLE.vessel_proxy("PUT", req.params["v"], req.params["rest"], req.query, req.json())


@app.route("GET", "/health")
def health(req):
    return {"service": "central-office", "status": "UP", "db": DB.scalar("SELECT COUNT(*) FROM vessels") is not None}


if __name__ == "__main__":
    app.serve(os.environ.get("D5_CENTRAL_HOST", CFG["host"]), int(CFG["port"]))
