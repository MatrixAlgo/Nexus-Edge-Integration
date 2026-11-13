"""Management loop: heartbeat, configuration + agent-version commands, office->vessel downlink.

Everything here travels over the same simulated satellite link as the business events, so a disconnected
vessel receives no configuration, versions or office changes until the link returns. Nothing received from
central is ever executed as a shell command: configuration is schema-validated data, packages are
checksum-verified data that switch the active (mock) agent version with rollback.
"""
import base64, hashlib, json, logging, os, threading, time

from common.edgeconfig import merged_with_defaults, validate_config
from common.util import dumps, loads, log, now_iso
from common.web import TransportError, call

L = logging.getLogger("edge.management")


class Management(threading.Thread):
    def __init__(self, store, dispatcher, cfg, metrics):
        super().__init__(name="management", daemon=True)
        self.store, self.dispatcher, self.metrics = store, dispatcher, metrics
        self.base = cfg["satelliteLinkUrl"] + cfg["mgmtPrefix"]
        self.vid = cfg["vesselId"]
        self.data_dir = cfg["dataDirAbs"]
        self.running = True

    def _call(self, method, path, body=None):
        return call(method, self.base + path, body, timeout=self.store.config()["requestTimeoutSec"])

    def run(self):
        while self.running:
            try:
                self.cycle()
                if self.store.link_up():
                    log(L, "satellite link restored (management probe)")
                if self.dispatcher.backoff_until:
                    self.dispatcher.link_restored()  # skip the remaining back-off: the link is demonstrably up
            except TransportError as e:
                if self.store.link_down():
                    log(L, "satellite link lost (management probe)", error=str(e))
            except Exception:
                L.exception("management cycle failed")
            time.sleep(self.store.config()["heartbeatSec"])

    def cycle(self):
        hb = {"vesselId": self.vid, "agentVersion": self.active_version(), "configVersion": self.store.config_version(),
              "metrics": self.metrics.snapshot(), "queue": self.store.queue_counts(), "sentAt": now_iso()}
        self._call("POST", "/vessels/%s/heartbeat" % self.vid, hb)
        st, cmds, _ = self._call("GET", "/vessels/%s/commands" % self.vid)
        if st == 200:
            for c in cmds.get("config", []):
                self.apply_config(c)
            for c in cmds.get("versions", []):
                self.apply_version(c)
        self.pull_downlink()

    # ------------------------------------------------------------------ configuration
    def apply_config(self, cmd):
        ver, cfg = cmd["configVersion"], cmd["config"]
        errs = validate_config(cfg)
        db = self.store.db
        if errs or ver <= self.store.config_version():
            detail = {"errors": errs} if errs else {"note": "version %s is not newer than active %s" % (ver, self.store.config_version())}
            status = "FAILED" if errs else "APPLIED"
            with db.tx() as c:
                c.execute("""INSERT OR REPLACE INTO edge_configuration(version,config,received_at,status,active,detail)
                    VALUES(?,?,?,?,0,?)""", (ver, dumps(cfg), now_iso(), "REJECTED" if errs else "SKIPPED", dumps(detail)))
        else:
            with db.tx() as c:
                c.execute("UPDATE edge_configuration SET active=0 WHERE active=1")
                c.execute("""INSERT OR REPLACE INTO edge_configuration(version,config,received_at,applied_at,status,active)
                    VALUES(?,?,?,?,'APPLIED',1)""", (ver, dumps(merged_with_defaults(cfg)), now_iso(), now_iso()))
            self.store.reload_config()
            status, detail = "APPLIED", {"activeConfigVersion": ver}
            log(L, "configuration applied", configVersion=ver)
        self._call("POST", "/vessels/%s/commands/config/%d" % (self.vid, cmd["deploymentId"]), {"status": status, "detail": detail})

    # ------------------------------------------------------------------ agent versions (controlled, no code execution)
    def active_version(self):
        r = self.store.db.q1("SELECT version FROM edge_versions WHERE active=1")
        return r["version"] if r else None

    def _report(self, dep, status, detail, from_version=None):
        self._call("POST", "/vessels/%s/commands/version/%d" % (self.vid, dep),
                   {"status": status, "detail": detail, "fromVersion": from_version})

    def apply_version(self, cmd):
        dep, db = cmd["deploymentId"], self.store.db
        current = self.active_version()
        if cmd["action"] == "ROLLBACK":
            cur = db.q1("SELECT * FROM edge_versions WHERE active=1")
            prev = cur and cur["previous_version"]
            if not prev or not db.q1("SELECT 1 FROM edge_versions WHERE version=?", (prev,)):
                return self._report(dep, "FAILED", {"error": "no previous version to roll back to"}, current)
            with db.tx() as c:
                c.execute("UPDATE edge_versions SET active=0, status='ROLLED_BACK' WHERE version=?", (current,))
                c.execute("UPDATE edge_versions SET active=1, status='ACTIVE', activated_at=? WHERE version=?", (now_iso(), prev))
            log(L, "agent rolled back", fromVersion=current, toVersion=prev)
            return self._report(dep, "ROLLED_BACK", {"activeVersion": prev, "rolledBackFrom": current}, current)

        version, expected = cmd["version"], cmd.get("sha256")
        if version == current:
            return self._report(dep, "APPLIED", {"note": "already active", "activeVersion": version}, current)
        self._report(dep, "DOWNLOADING", {"version": version}, current)
        st, pkg, _ = self._call("GET", "/packages/%s" % version)
        if st != 200:
            return self._report(dep, "FAILED", {"error": "package download failed (HTTP %s)" % st}, current)
        raw = base64.b64decode(pkg["packageBase64"])
        actual = hashlib.sha256(raw).hexdigest()
        if not expected or actual != expected:
            log(L, "package checksum mismatch - update refused", logging.WARNING, version=version, expected=expected, actual=actual)
            with db.tx() as c:
                c.execute("""INSERT OR REPLACE INTO edge_versions(version,sha256,status,active,detail) VALUES(?,?,'REJECTED',0,?)""",
                          (version, actual, dumps({"error": "checksum mismatch", "expected": expected, "actual": actual})))
            return self._report(dep, "FAILED", {"error": "checksum mismatch - package rejected, still on %s" % current,
                                                "expected": expected, "actual": actual}, current)
        try:
            doc = json.loads(raw)
            manifest = doc["manifest"]
            assert manifest["name"] == "d5-edge-agent" and manifest["version"] == version
        except (ValueError, KeyError, AssertionError, TypeError):
            return self._report(dep, "FAILED", {"error": "package manifest invalid"}, current)
        stage = os.path.join(self.data_dir, "versions", version)
        os.makedirs(stage, exist_ok=True)
        with open(os.path.join(stage, "package.json"), "wb") as f:
            f.write(raw)
        with db.tx() as c:
            c.execute("""INSERT OR REPLACE INTO edge_versions(version,sha256,manifest,staged_path,staged_at,status,active,previous_version)
                VALUES(?,?,?,?,?,'STAGED',0,?)""", (version, actual, dumps(manifest), stage, now_iso(), current))
        self._report(dep, "STAGED", {"version": version, "sha256": actual}, current)
        # activate atomically, then run the post-activation health check; roll back automatically on failure
        with db.tx() as c:
            c.execute("UPDATE edge_versions SET active=0, status='INACTIVE' WHERE active=1")
            c.execute("UPDATE edge_versions SET active=1, status='ACTIVE', activated_at=? WHERE version=?", (now_iso(), version))
        ok, why = self.health_check(manifest)
        if not ok:
            with db.tx() as c:
                c.execute("UPDATE edge_versions SET active=0, status='FAILED_HEALTHCHECK', detail=? WHERE version=?",
                          (dumps({"error": why}), version))
                c.execute("UPDATE edge_versions SET active=1, status='ACTIVE' WHERE version=?", (current,))
            log(L, "health check failed - automatic rollback", logging.WARNING, version=version, restored=current)
            return self._report(dep, "FAILED", {"error": "health check failed: %s" % why,
                                                "automaticRollback": True, "activeVersion": current}, current)
        log(L, "agent version applied", version=version, previous=current)
        self._report(dep, "APPLIED", {"activeVersion": version, "previousVersion": current}, current)

    def health_check(self, manifest):
        if manifest.get("healthCheck") != "pass":
            return False, "package self-test reported failure"
        if self.store.db.scalar("PRAGMA quick_check") != "ok":
            return False, "local database integrity check failed"
        if not self.dispatcher.is_alive():
            return False, "dispatcher thread not running"
        return True, None

    # ------------------------------------------------------------------ office -> vessel record changes
    def pull_downlink(self):
        after = self.store.get_state("downlink_cursor", 0)
        st, body, _ = self._call("GET", "/vessels/%s/downlink?after=%d" % (self.vid, after))
        if st != 200:
            return
        db = self.store.db
        for it in body.get("items", []):
            busy = db.q1("SELECT 1 FROM outbound_events WHERE record_id=? AND status IN ('PENDING','SENDING','ACCEPTED')",
                         (it["record_id"],))
            if busy:
                break  # apply after our own pending change for this record has been acknowledged (keeps order)
            with db.tx() as c:
                cur = c.execute("SELECT version FROM operational_records WHERE record_id=?", (it["record_id"],)).fetchone()
                sync = "CONFLICT_REVIEW" if it.get("conflict_open") else ("FROM_OFFICE" if it["reason"].startswith("OFFICE") else "SYNCED")
                if cur is None:
                    c.execute("""INSERT INTO operational_records(record_id,record_type,vessel_id,source_system,priority,version,
                        payload,created_at,updated_at,updated_by,sync_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (it["record_id"], it["record_type"], self.vid, it["source_system"], it["priority"], it["version"],
                               dumps(it["payload"]), it["record_created_at"], it["created_at"], it["updated_by"], sync))
                    outcome = "INSERTED"
                elif it["version"] >= cur["version"]:
                    c.execute("""UPDATE operational_records SET payload=?, version=?, updated_at=?, updated_by=?, sync_status=?
                        WHERE record_id=?""", (dumps(it["payload"]), it["version"], it["created_at"], it["updated_by"], sync,
                                               it["record_id"]))
                    outcome = "UPDATED"
                else:
                    outcome = "IGNORED_OLDER"
                c.execute("INSERT OR REPLACE INTO applied_downlink(id,record_id,version,reason,applied_at,outcome) VALUES(?,?,?,?,?,?)",
                          (it["id"], it["record_id"], it["version"], it["reason"], now_iso(), outcome))
                self.store.set_state("downlink_cursor", it["id"], c)
            log(L, "office change applied onboard", recordId=it["record_id"], version=it["version"], reason=it["reason"],
                outcome=outcome)
