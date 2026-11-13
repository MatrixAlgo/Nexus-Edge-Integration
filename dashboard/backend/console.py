"""Management-console aggregation for the dashboard.

Two kinds of vessel information are shown and kept apart:
  * "reported"  - what the vessel told the central office over the satellite link (heartbeats, acks). Goes stale
                   when the link is down, exactly as it would in production.
  * "observer"  - a direct read of the edge agent's local API on this demo host (the vessel's own console/LAN).
                   It exists only so the demo can show what is happening onboard while the satellite is down.
"""
import subprocess, threading, time

from common.util import loads, now_iso, seconds_since
from common.web import TransportError, call
from processing import audit

MQ_QUEUES = ("D5.IN.CRITICAL", "D5.IN.NORMAL", "D5.IN.LOW", "D5.OUT.ACK", "D5.ERROR", "D5.DLQ")
ACE_APP = "D5_EdgeSync"


class Console:
    def __init__(self, db, cfg):
        self.db, self.cfg = db, cfg
        self._cache, self._lock = {}, threading.Lock()

    def _cached(self, key, ttl, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    # ------------------------------------------------------------------ sources
    def _edge(self, vessel_id, path, timeout=2.0):
        v = self.db.q1("SELECT edge_port FROM vessels WHERE vessel_id=?", (vessel_id,))
        if not v:
            return None
        try:
            st, body, _ = call("GET", "http://127.0.0.1:%d/api/%s" % (v["edge_port"], path), timeout=timeout)
            return body if st == 200 else None
        except TransportError:
            return None

    def _links(self):
        def get():
            try:
                st, body, _ = call("GET", self.cfg["networkSimulatorUrl"] + "/links", timeout=2)
                return body if st == 200 else {}
            except TransportError:
                return {}
        return self._cached("links", 0.5, get)

    def _observer(self, vessel_id):
        return self._cached("obs:" + vessel_id, 0.8, lambda: self._edge(vessel_id, "status"))

    # ------------------------------------------------------------------ pages
    def vessels(self):
        links = self._links()
        out = []
        stale = self.cfg.get("heartbeatStaleSec", 20)
        for v in self.db.q("SELECT * FROM vessels ORDER BY vessel_id"):
            hb = loads(v["heartbeat"]) or {}
            age = seconds_since(v["last_heartbeat_at"])
            obs = self._observer(v["vessel_id"])
            link = links.get(v["vessel_id"], {})
            out.append({
                "vesselId": v["vessel_id"], "name": v["name"], "group": v["vessel_group"],
                "link": link,
                "connectivity": "ONLINE" if link.get("state") == "CONNECTED" else
                                ("INTERMITTENT" if link.get("state") == "INTERMITTENT" else "OFFLINE"),
                "agentReachable": obs is not None,
                "reported": {"lastHeartbeatAt": v["last_heartbeat_at"], "heartbeatAgeSec": age,
                             "stale": age is None or age > stale, "agentVersion": v["agent_version"],
                             "configVersion": v["config_version"], "metrics": hb.get("metrics"),
                             "queue": hb.get("queue")},
                "observer": obs,
            })
        return out

    def overview(self):
        vs = self.vessels()
        q = {"pending": 0, "inFlight": 0, "awaitingAck": 0, "acknowledged": 0, "failed": 0}
        for v in vs:
            for k in q:
                q[k] += ((v["observer"] or {}).get("queue") or {}).get(k, 0)
        led = self.db.q1("""SELECT SUM(status='PROCESSED') processed, SUM(status='REJECTED') rejected,
            SUM(status='PROCESSED' AND ack_recorded_at IS NULL) ack_pending, SUM(received_count-1) dup_deliveries
            FROM event_ledger""") or {}
        cf = self.db.q1("SELECT COUNT(*) total, SUM(status='OPEN') open, SUM(status='AUTO_RESOLVED') auto, "
                        "SUM(status='RESOLVED') resolved FROM conflicts") or {}
        return {
            "generatedAt": now_iso(),
            "totalVessels": len(vs),
            "onlineVessels": sum(1 for v in vs if v["connectivity"] == "ONLINE"),
            "offlineVessels": sum(1 for v in vs if v["connectivity"] != "ONLINE"),
            "queuedEvents": q["pending"] + q["inFlight"],
            "pendingAcknowledgements": q["awaitingAck"],
            "synchronizedEvents": led.get("processed") or 0,
            "failedEvents": q["failed"],
            "edgeQueue": q,
            "centralLedger": {"processed": led.get("processed") or 0, "rejected": led.get("rejected") or 0,
                              "ackPending": led.get("ack_pending") or 0,
                              "duplicateDeliveries": led.get("dup_deliveries") or 0},
            "conflicts": {"total": cf.get("total") or 0, "open": cf.get("open") or 0,
                          "autoResolved": cf.get("auto") or 0, "resolved": cf.get("resolved") or 0},
            "centralRecords": self.db.scalar("SELECT COUNT(*) FROM records"),
            "businessTransactions": self.db.scalar("SELECT COUNT(*) FROM business_transactions"),
            "vessels": vs,
            "platform": self.platform(),
        }

    def events(self, vessel=None, status=None, limit=300):
        ids = [vessel] if vessel else [r["vessel_id"] for r in self.db.q("SELECT vessel_id FROM vessels")]
        rows = []
        for vid in ids:
            qs = "events?limit=%d" % limit + ("&status=" + status if status else "")
            evs = self._edge(vid, qs) or []
            for e in evs:
                e["vesselId"] = vid
            rows.extend(evs)
        if rows:
            led = {}
            ev_ids = [e["eventId"] for e in rows]
            for i in range(0, len(ev_ids), 500):
                chunk = ev_ids[i:i + 500]
                for r in self.db.q("SELECT event_id,status,outcome,transaction_id,processed_at,received_count,"
                                   "ack_recorded_at,processing_order FROM event_ledger WHERE event_id IN (%s)"
                                   % ",".join("?" * len(chunk)), chunk):
                    led[r["event_id"]] = r
            for e in rows:
                e["central"] = led.get(e["eventId"])
        rows.sort(key=lambda e: e.get("createdAt") or "", reverse=True)
        return rows[:limit]

    def sync(self):
        out = []
        links = self._links()
        for v in self.db.q("SELECT vessel_id, name FROM vessels ORDER BY vessel_id"):
            vid = v["vessel_id"]
            sync = self._edge(vid, "sync") or {}
            seq = self.db.q("""SELECT event_id, record_id, record_type, priority, sequence_number, outcome, processed_at,
                processing_order, received_count, ack_recorded_at FROM event_ledger WHERE vessel_id=? AND status='PROCESSED'
                ORDER BY processing_order DESC LIMIT 40""", (vid,))
            out.append({"vesselId": vid, "name": v["name"], "link": links.get(vid, {}), "edge": sync,
                        "centralDeliverySequence": seq})
        return out

    def central_records(self, vessel=None):
        sql = "SELECT * FROM records" + (" WHERE vessel_id=?" if vessel else "") + " ORDER BY updated_at DESC"
        rows = self.db.q(sql, (vessel,) if vessel else ())
        for r in rows:
            r["payload"] = loads(r["payload"])
        return rows

    def central_record(self, rid):
        r = self.db.q1("SELECT * FROM records WHERE record_id=?", (rid,))
        if not r:
            return 404, {"error": "not found"}
        r["payload"] = loads(r["payload"])
        r["versions"] = [dict(x, payload=loads(x["payload"])) for x in
                         self.db.q("SELECT * FROM record_versions WHERE record_id=? ORDER BY version", (rid,))]
        r["transactions"] = self.db.q("SELECT * FROM business_transactions WHERE record_id=? ORDER BY created_at", (rid,))
        r["audit"] = self.db.q("SELECT * FROM audit_log WHERE entity_id=? OR detail LIKE ? ORDER BY id",
                               (rid, '%"' + rid + '"%'))
        return r

    def ledger(self, limit=300):
        return self.db.q("SELECT * FROM event_ledger ORDER BY first_received_at DESC LIMIT ?", (limit,))

    def conflicts(self):
        rows = self.db.q("SELECT * FROM conflicts ORDER BY detected_at DESC")
        for r in rows:
            for k in ("base_payload", "office_payload", "vessel_payload", "merged_payload", "final_payload",
                      "field_decisions"):
                r[k] = loads(r[k])
        return rows

    def config_state(self):
        vers = self.db.q("SELECT * FROM config_versions ORDER BY version DESC")
        for v in vers:
            v["config"] = loads(v["config"])
        deps = self.db.q("SELECT * FROM config_deployments ORDER BY id DESC LIMIT 200")
        vessels = self.db.q("SELECT vessel_id, name, vessel_group, config_version, last_heartbeat_at FROM vessels")
        for v in vessels:
            obs = self._observer(v["vessel_id"])
            v["activeConfigVersion"] = (obs or {}).get("configVersion")
        return {"versions": vers, "deployments": deps, "vessels": vessels,
                "groups": sorted({v["vessel_group"] for v in vessels})}

    def version_state(self):
        pk = self.db.q("SELECT version, manifest, sha256, created_at, created_by, notes, length(package) size FROM agent_packages "
                       "ORDER BY created_at DESC")
        for p in pk:
            p["manifest"] = loads(p["manifest"])
        deps = self.db.q("SELECT * FROM version_deployments ORDER BY id DESC LIMIT 200")
        for d in deps:
            d["detail"] = loads(d["detail"])
        vessels = self.db.q("SELECT vessel_id, name, vessel_group, agent_version FROM vessels")
        for v in vessels:
            obs = self._observer(v["vessel_id"])
            v["activeVersion"] = (obs or {}).get("agentVersion")
            v["previousVersion"] = (obs or {}).get("previousAgentVersion")
        return {"packages": pk, "deployments": deps, "vessels": vessels,
                "groups": sorted({v["vessel_group"] for v in vessels})}

    def network_get(self):
        return self._links()

    def network_set(self, vessel_id, body, actor):
        try:
            st, res, _ = call("POST", self.cfg["networkSimulatorUrl"] + "/links/" + vessel_id, body, timeout=3)
        except TransportError as e:
            return 503, {"error": "network simulator unavailable", "detail": str(e)}
        if st == 200:
            with self.db.tx() as c:
                audit(c, actor, "SATELLITE_LINK_CHANGED", "vessel", vessel_id, body)
            with self._lock:
                self._cache.pop("links", None)
        return st, res

    def vessel_proxy(self, method, vessel_id, rest, query, body):
        v = self.db.q1("SELECT edge_port FROM vessels WHERE vessel_id=?", (vessel_id,))
        if not v:
            return 404, {"error": "unknown vessel"}
        qs = "&".join("%s=%s" % (k, val) for k, val in query.items())
        try:
            st, res, _ = call(method, "http://127.0.0.1:%d/api/%s%s" % (v["edge_port"], rest, "?" + qs if qs else ""),
                              body, timeout=10)
        except TransportError as e:
            return 503, {"error": "edge agent %s not reachable" % vessel_id, "detail": str(e)}
        return st, res

    # ------------------------------------------------------------------ IBM platform status (real, read-only)
    def platform(self):
        return self._cached("platform", 3.0, self._platform)

    def _platform(self):
        out = {"queueManager": self.cfg["queueManager"], "queues": {}, "mqStatus": "UNKNOWN", "ace": {}}
        try:
            p = subprocess.run(["/opt/mqm/bin/runmqsc", "-e", self.cfg["queueManager"]],
                               input="DISPLAY QLOCAL('D5.*') CURDEPTH\n", capture_output=True, text=True, timeout=10)
            cur = None
            for tok in p.stdout.replace("\n", " ").split():
                if tok.startswith("QUEUE("):
                    cur = tok[6:-1]
                elif tok.startswith("CURDEPTH(") and cur:
                    out["queues"][cur] = int(tok[9:-1])
            out["mqStatus"] = "RUNNING" if out["queues"] else "UNAVAILABLE"
        except (OSError, subprocess.SubprocessError) as e:
            out["mqStatus"] = "UNAVAILABLE: %s" % e
        try:
            st, body, _ = call("GET", self.cfg["aceAdminUrl"].replace("http://", "https://") +
                               "/apiv2/applications/%s/messageflows" % ACE_APP, timeout=3)
            if st == 200:
                flows = []
                for c in (body or {}).get("children", []):
                    s2, fb, _ = call("GET", self.cfg["aceAdminUrl"].replace("http://", "https://") +
                                     "/apiv2/applications/%s/messageflows/%s" % (ACE_APP, c["name"]), timeout=3)
                    flows.append({"name": c["name"], "running": bool(s2 == 200 and (fb.get("active") or {}).get("isRunning"))})
                out["ace"] = {"integrationServer": "IS_D5_EDGE", "status": "RUNNING", "application": ACE_APP,
                              "flows": flows}
            else:
                out["ace"] = {"integrationServer": "IS_D5_EDGE", "status": "RUNNING",
                              "application": ACE_APP + " (not deployed)", "flows": []}
        except TransportError:
            out["ace"] = {"integrationServer": "IS_D5_EDGE", "status": "DOWN", "flows": []}
        return out
