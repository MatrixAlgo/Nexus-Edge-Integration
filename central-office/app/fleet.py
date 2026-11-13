"""Fleet management: vessel registry and heartbeats, configuration versions, agent packages, downlink feed."""
import base64, hashlib, json, re

from common.edgeconfig import DEFAULT_CONFIG, merged_with_defaults, validate_config
from common.util import dumps, loads, now_iso
from processing import audit

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def seed(db, cfg):
    with db.tx() as c:
        for v in cfg["vessels"]:
            c.execute("""INSERT INTO vessels(vessel_id,name,vessel_group,edge_port) VALUES(?,?,?,?)
                ON CONFLICT(vessel_id) DO UPDATE SET name=excluded.name, vessel_group=excluded.vessel_group,
                edge_port=excluded.edge_port""", (v["vesselId"], v["name"], v["group"], v["edgePort"]))
        if not c.execute("SELECT 1 FROM config_versions WHERE version=1").fetchone():
            c.execute("INSERT INTO config_versions(version,config,created_at,created_by,notes,target) VALUES(1,?,?,?,?,?)",
                      (dumps(DEFAULT_CONFIG), now_iso(), "system", "factory default configuration", "ALL"))
        if not c.execute("SELECT 1 FROM agent_packages WHERE version='1.0.0'").fetchone():
            _store_package(c, "1.0.0", {"features": ["outbox", "priority-dispatch", "ack-tracking"]}, "system",
                           "baseline edge agent", False, False)


def resolve_targets(db, target):
    """target: {'vessels': [...]} and/or {'group': 'GULF-FLEET'|'ALL'}."""
    ids = set(target.get("vessels") or [])
    g = target.get("group")
    if g == "ALL":
        ids |= {r["vessel_id"] for r in db.q("SELECT vessel_id FROM vessels")}
    elif g:
        ids |= {r["vessel_id"] for r in db.q("SELECT vessel_id FROM vessels WHERE vessel_group=?", (g,))}
    known = {r["vessel_id"] for r in db.q("SELECT vessel_id FROM vessels")}
    return sorted(ids & known), sorted(ids - known)


def heartbeat(db, vessel_id, hb):
    with db.tx() as c:
        n = c.execute("UPDATE vessels SET last_heartbeat_at=?, agent_version=?, config_version=?, heartbeat=? WHERE vessel_id=?",
                      (now_iso(), hb.get("agentVersion"), hb.get("configVersion"), dumps(hb), vessel_id)).rowcount
    return (200, {"ok": True, "serverTime": now_iso()}) if n else (404, {"error": "unknown vessel"})


def publish_config(db, body, actor):
    cfg = body.get("config")
    errs = validate_config(cfg)
    if errs:
        return 400, {"error": "invalid configuration", "detail": errs}
    vessels, unknown = resolve_targets(db, body.get("target") or {})
    if unknown or not vessels:
        return 400, {"error": "no valid target vessels", "detail": unknown}
    full = merged_with_defaults(cfg)
    ts = now_iso()
    with db.tx() as c:
        ver = (c.execute("SELECT MAX(version) FROM config_versions").fetchone()[0] or 0) + 1
        c.execute("INSERT INTO config_versions(version,config,created_at,created_by,notes,target) VALUES(?,?,?,?,?,?)",
                  (ver, dumps(full), ts, actor, body.get("notes"), dumps(body.get("target"))))
        for v in vessels:
            c.execute("""UPDATE config_deployments SET status='SUPERSEDED', updated_at=? WHERE vessel_id=? AND status='PENDING'""",
                      (ts, v))
            c.execute("""INSERT INTO config_deployments(config_version,vessel_id,status,created_at,updated_at)
                VALUES(?,?,'PENDING',?,?)""", (ver, v, ts, ts))
        audit(c, actor, "CONFIG_PUBLISHED", "config", str(ver), {"vessels": vessels, "notes": body.get("notes")})
    return 200, {"configVersion": ver, "vessels": vessels, "config": full}


def _store_package(c, version, manifest_extra, actor, notes, corrupt, fail_health):
    manifest = {"name": "d5-edge-agent", "version": version, "builtAt": now_iso(), "minPython": "3.9",
                "healthCheck": "fail" if fail_health else "pass"}
    manifest.update(manifest_extra or {})
    pkg = json.dumps({"manifest": manifest, "files": {"agent/feature_flags.json": manifest.get("features", [])}},
                     sort_keys=True).encode()
    sha = hashlib.sha256(pkg).hexdigest()
    if corrupt:  # demo: the published checksum no longer matches the bytes the vessel will download
        pkg = pkg.replace(b'"manifest"', b'"manifesT"', 1)
    c.execute("INSERT INTO agent_packages(version,manifest,package,sha256,created_at,created_by,notes) VALUES(?,?,?,?,?,?,?)",
              (version, dumps(manifest), pkg, sha, now_iso(), actor, notes))
    return manifest, sha


def publish_version(db, body, actor):
    version = str(body.get("version", ""))
    if not SEMVER.match(version):
        return 400, {"error": "version must be MAJOR.MINOR.PATCH"}
    vessels, unknown = resolve_targets(db, body.get("target") or {})
    if unknown or not vessels:
        return 400, {"error": "no valid target vessels", "detail": unknown}
    ts = now_iso()
    with db.tx() as c:
        if c.execute("SELECT 1 FROM agent_packages WHERE version=?", (version,)).fetchone():
            if body.get("corrupt") or body.get("failHealthCheck"):
                return 409, {"error": "version %s already published; use a new version number for a failure demo" % version}
            sha = c.execute("SELECT sha256 FROM agent_packages WHERE version=?", (version,)).fetchone()[0]
        else:
            _, sha = _store_package(c, version, {"features": body.get("features") or ["outbox", "priority-dispatch",
                                    "ack-tracking", "delta-compression"]}, actor, body.get("notes"),
                                    bool(body.get("corrupt")), bool(body.get("failHealthCheck")))
        for v in vessels:
            c.execute("""INSERT INTO version_deployments(vessel_id,action,target_version,status,created_at,updated_at)
                VALUES(?,'UPDATE',?,'PENDING',?,?)""", (v, version, ts, ts))
        audit(c, actor, "VERSION_PUBLISHED", "agent", version, {"vessels": vessels, "sha256": sha})
    return 200, {"version": version, "sha256": sha, "vessels": vessels}


def request_rollback(db, body, actor):
    vessels, unknown = resolve_targets(db, body.get("target") or {})
    if unknown or not vessels:
        return 400, {"error": "no valid target vessels", "detail": unknown}
    ts = now_iso()
    with db.tx() as c:
        for v in vessels:
            c.execute("""INSERT INTO version_deployments(vessel_id,action,target_version,status,created_at,updated_at)
                VALUES(?,'ROLLBACK',NULL,'PENDING',?,?)""", (v, ts, ts))
        audit(c, actor, "ROLLBACK_REQUESTED", "agent", ",".join(vessels), None)
    return 200, {"vessels": vessels}


def pending_commands(db, vessel_id):
    cfg = db.q("""SELECT d.id, d.config_version, c.config FROM config_deployments d JOIN config_versions c
        ON c.version=d.config_version WHERE d.vessel_id=? AND d.status='PENDING' ORDER BY d.id""", (vessel_id,))
    ver = db.q("""SELECT d.id, d.action, d.target_version, p.sha256 FROM version_deployments d
        LEFT JOIN agent_packages p ON p.version=d.target_version WHERE d.vessel_id=? AND d.status IN ('PENDING')
        ORDER BY d.id""", (vessel_id,))
    return {"config": [{"deploymentId": r["id"], "configVersion": r["config_version"], "config": loads(r["config"])} for r in cfg],
            "versions": [{"deploymentId": r["id"], "action": r["action"], "version": r["target_version"],
                          "sha256": r["sha256"]} for r in ver]}


def report_command(db, vessel_id, kind, dep_id, body):
    table = {"config": "config_deployments", "version": "version_deployments"}.get(kind)
    if not table:
        return 404, {"error": "unknown command kind"}
    status = body.get("status")
    allowed = {"config": ("APPLIED", "FAILED"),
               "version": ("DOWNLOADING", "STAGED", "APPLIED", "FAILED", "ROLLED_BACK")}[kind]
    if status not in allowed:
        return 400, {"error": "invalid status"}
    with db.tx() as c:
        sets = "status=?, detail=?, updated_at=?" + (", from_version=?" if kind == "version" else "")
        args = [status, dumps(body.get("detail")), now_iso()] + ([body.get("fromVersion")] if kind == "version" else [])
        n = c.execute("UPDATE %s SET %s WHERE id=? AND vessel_id=?" % (table, sets), args + [dep_id, vessel_id]).rowcount
        if not n:
            return 404, {"error": "deployment not found"}
        audit(c, vessel_id, "%s_DEPLOYMENT_%s" % (kind.upper(), status), kind, str(dep_id), body.get("detail"))
    return 200, {"ok": True}


def get_package(db, version):
    r = db.q1("SELECT version, package, sha256, manifest FROM agent_packages WHERE version=?", (version,))
    if not r:
        return 404, {"error": "package not found"}
    return 200, {"version": r["version"], "sha256": r["sha256"], "packageBase64": base64.b64encode(r["package"]).decode()}


def downlink(db, vessel_id, after):
    rows = db.q("""SELECT f.id, f.record_id, f.version, f.payload, f.reason, f.created_at, r.record_type, r.priority,
        r.source_system, r.created_at AS record_created_at, r.updated_by, r.conflict_open FROM downlink_feed f JOIN records r ON r.record_id=f.record_id
        WHERE f.vessel_id=? AND f.id>? ORDER BY f.id LIMIT 100""", (vessel_id, after))
    for r in rows:
        r["payload"] = loads(r["payload"])
    return {"items": rows}
