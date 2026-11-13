"""Edge local store: operational records + transactional outbox (SQLite).

A record change and its outbound event are written in ONE transaction, so a business change can never exist
without an event to synchronise it (and vice versa). Outbound events are never deleted by the agent.
"""
import json, os, random, threading

from common.db import Database
from common.edgeconfig import DEFAULT_CONFIG, merged_with_defaults
from common.util import PRIORITY_RANK, RECORD_TYPES, dumps, loads, new_id, now_iso

PREFIX = {"PR": ("PR", 1001), "MAINTENANCE": ("MNT", 2001), "GRN": ("GRN", 3001), "EMERGENCY_MAINTENANCE": ("EMR", 4001)}
OPEN_STATES = ("PENDING", "SENDING", "ACCEPTED")


class EdgeStore:
    def __init__(self, path, vessel_id, initial_version="1.0.0"):
        self.vessel_id = vessel_id
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "database", "schema.sql")) as f:
            self.db = Database(path, f.read())
        self._cfg_lock = threading.Lock()
        self._cfg = None
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM edge_configuration").fetchone():
                c.execute("""INSERT INTO edge_configuration(version,config,received_at,applied_at,status,active)
                    VALUES(1,?,?,?,'APPLIED',1)""", (dumps(DEFAULT_CONFIG), now_iso(), now_iso()))
            if not c.execute("SELECT 1 FROM edge_versions").fetchone():
                c.execute("""INSERT INTO edge_versions(version,manifest,activated_at,status,active)
                    VALUES(?,?,?,'ACTIVE',1)""", (initial_version, dumps({"name": "d5-edge-agent", "version": initial_version,
                                                   "features": ["outbox", "priority-dispatch", "ack-tracking"]}), now_iso()))
            # an event caught mid-send by a crash has an unknown outcome: it will simply be sent again (idempotent centrally)
            c.execute("UPDATE outbound_events SET status='PENDING', updated_at=? WHERE status='SENDING'", (now_iso(),))

    # ------------------------------------------------------------------ configuration
    def config(self):
        with self._cfg_lock:
            if self._cfg is None:
                r = self.db.q1("SELECT version, config FROM edge_configuration WHERE active=1")
                self._cfg = (r["version"], merged_with_defaults(loads(r["config"])))
            return self._cfg[1]

    def config_version(self):
        self.config()
        return self._cfg[0]

    def reload_config(self):
        with self._cfg_lock:
            self._cfg = None
        return self.config()

    # ------------------------------------------------------------------ sync_state key/value
    def get_state(self, key, default=None):
        r = self.db.q1("SELECT value FROM sync_state WHERE key=?", (key,))
        return loads(r["value"]) if r else default

    def set_state(self, key, value, c=None):
        sql = ("INSERT INTO sync_state(key,value,updated_at) VALUES(?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at")
        (c or self.db.conn()).execute(sql, (key, json.dumps(value), now_iso()))

    # ------------------------------------------------------------------ business operations
    def _next_record_id(self, c, rtype):
        pfx, start = PREFIX[rtype]
        key = "next_id_" + pfx
        r = c.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        n = json.loads(r[0]) if r else start + (0 if self.vessel_id.endswith("01") else 500)
        while c.execute("SELECT 1 FROM operational_records WHERE record_id=?", ("%s-%d" % (pfx, n),)).fetchone():
            n += 1
        self.set_state(key, n + 1, c)
        return "%s-%d" % (pfx, n)

    def _event(self, c, rec, event_type, base_version=None, base_snapshot=None, changed=None):
        seq = (c.execute("SELECT MAX(sequence_number) FROM outbound_events").fetchone()[0] or 0) + 1
        ts = now_iso()
        evt = {"eventId": new_id(), "eventType": event_type, "recordId": rec["record_id"], "recordType": rec["record_type"],
               "vesselId": self.vessel_id, "sourceSystem": "VESSEL_AMOS", "priority": rec["priority"],
               "sequenceNumber": seq, "version": rec["version"], "createdAt": ts, "updatedAt": rec["updated_at"],
               "updatedBy": rec["updated_by"], "recordCreatedAt": rec["created_at"], "payload": rec["payload"]}
        if event_type == "RECORD_UPDATED":
            evt.update(baseVersion=base_version, baseSnapshot=base_snapshot, changedFields=changed)
        c.execute("""INSERT INTO outbound_events(event_id,sequence_number,record_id,record_type,event_type,priority,priority_rank,
            version,base_version,event_json,created_at,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'PENDING',?)""",
                  (evt["eventId"], seq, rec["record_id"], rec["record_type"], event_type, rec["priority"],
                   PRIORITY_RANK[rec["priority"]], rec["version"], base_version, dumps(evt), ts, ts))
        return evt

    def create_record(self, data, actor):
        rtype = data.get("recordType")
        if rtype not in RECORD_TYPES:
            return 400, {"error": "recordType must be one of " + ", ".join(RECORD_TYPES)}
        prio = data.get("priority") or self.config()["priorityRules"].get(rtype, "NORMAL")
        if prio not in PRIORITY_RANK:
            return 400, {"error": "priority must be CRITICAL, NORMAL or LOW"}
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            return 400, {"error": "payload must be an object"}
        ts = now_iso()
        with self.db.tx() as c:
            rid = data.get("recordId") or self._next_record_id(c, rtype)
            if c.execute("SELECT 1 FROM operational_records WHERE record_id=?", (rid,)).fetchone():
                return 409, {"error": "record %s already exists" % rid}
            rec = {"record_id": rid, "record_type": rtype, "priority": prio, "version": 1, "payload": payload,
                   "created_at": ts, "updated_at": ts, "updated_by": actor}
            c.execute("""INSERT INTO operational_records(record_id,record_type,vessel_id,source_system,priority,version,payload,
                created_at,updated_at,updated_by,sync_status) VALUES(?,?,?,'VESSEL_AMOS',?,1,?,?,?,?,'PENDING')""",
                      (rid, rtype, self.vessel_id, prio, dumps(payload), ts, ts, actor))
            evt = self._event(c, rec, "RECORD_CREATED")
        return 201, {"recordId": rid, "version": 1, "priority": prio, "eventId": evt["eventId"],
                     "sequenceNumber": evt["sequenceNumber"]}

    def update_record(self, rid, data, actor):
        changes = data.get("changes") or {}
        if not isinstance(changes, dict) or not changes:
            return 400, {"error": "changes must be a non-empty object"}
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM operational_records WHERE record_id=?", (rid,)).fetchone()
            if not r:
                return 404, {"error": "record not found"}
            base = loads(r["payload"], {})
            changed = {k: v for k, v in changes.items() if base.get(k) != v}
            if not changed:
                return 400, {"error": "no field value changed"}
            new = dict(base)
            new.update(changed)
            prio = data.get("priority") or r["priority"]
            if prio not in PRIORITY_RANK:
                return 400, {"error": "invalid priority"}
            ts = now_iso()
            ver = r["version"] + 1
            c.execute("""UPDATE operational_records SET payload=?, version=?, updated_at=?, updated_by=?, priority=?,
                sync_status='PENDING' WHERE record_id=?""", (dumps(new), ver, ts, actor, prio, rid))
            rec = {"record_id": rid, "record_type": r["record_type"], "priority": prio, "version": ver, "payload": new,
                   "created_at": r["created_at"], "updated_at": ts, "updated_by": actor}
            evt = self._event(c, rec, "RECORD_UPDATED", r["version"], base, changed)
        return 200, {"recordId": rid, "version": ver, "changedFields": changed, "eventId": evt["eventId"],
                     "sequenceNumber": evt["sequenceNumber"]}

    def records(self, rtype=None):
        sql = "SELECT * FROM operational_records" + (" WHERE record_type=?" if rtype else "") + " ORDER BY updated_at DESC"
        rows = self.db.q(sql, (rtype,) if rtype else ())
        for r in rows:
            r["payload"] = loads(r["payload"])
        return rows

    def record(self, rid):
        r = self.db.q1("SELECT * FROM operational_records WHERE record_id=?", (rid,))
        if r:
            r["payload"] = loads(r["payload"])
            r["events"] = self.db.q("SELECT event_id,event_type,version,status,created_at,acked_at,central_outcome "
                                    "FROM outbound_events WHERE record_id=? ORDER BY sequence_number", (rid,))
        return r

    # ------------------------------------------------------------------ outbox for the dispatcher
    def next_event(self):
        """Strict priority, one in flight: an unfinished in-flight event first; otherwise the highest-priority,
        oldest PENDING event - except that an earlier unsent event of the SAME record always goes first."""
        r = self.db.q1("SELECT * FROM outbound_events WHERE status IN ('SENDING','ACCEPTED') "
                       "ORDER BY priority_rank, sequence_number LIMIT 1")
        if r:
            return r
        r = self.db.q1("SELECT * FROM outbound_events WHERE status='PENDING' ORDER BY priority_rank, sequence_number LIMIT 1")
        if r:
            earlier = self.db.q1("SELECT * FROM outbound_events WHERE status='PENDING' AND record_id=? AND sequence_number<? "
                                 "ORDER BY sequence_number LIMIT 1", (r["record_id"], r["sequence_number"]))
            return earlier or r
        return None

    def mark(self, eid, **cols):
        cols["updated_at"] = now_iso()
        sets = ", ".join("%s=?" % k for k in cols)
        with self.db.tx() as c:
            c.execute("UPDATE outbound_events SET %s WHERE event_id=?" % sets, list(cols.values()) + [eid])

    def attempt(self, eid, attempt_no, phase, started, duration_ms, result, http_status=None, detail=None):
        self.db.conn().execute("""INSERT INTO delivery_attempts(event_id,attempt_no,phase,started_at,duration_ms,result,
            http_status,detail) VALUES(?,?,?,?,?,?,?,?)""",
                               (eid, attempt_no, phase, started, duration_ms, result, http_status,
                                detail if detail is None or isinstance(detail, str) else dumps(detail)))

    def acknowledge(self, ev, central):
        ts = now_iso()
        with self.db.tx() as c:
            order = (c.execute("SELECT MAX(delivery_order) FROM outbound_events").fetchone()[0] or 0) + 1
            c.execute("""UPDATE outbound_events SET status='ACKNOWLEDGED', acked_at=?, delivery_order=?, central_outcome=?,
                transaction_id=?, last_error=NULL, updated_at=? WHERE event_id=?""",
                      (ts, order, central.get("outcome"), central.get("transactionId"), ts, ev["event_id"]))
            still = c.execute("SELECT 1 FROM outbound_events WHERE record_id=? AND status IN ('PENDING','SENDING','ACCEPTED')",
                              (ev["record_id"],)).fetchone()
            if not still:
                c.execute("UPDATE operational_records SET sync_status=CASE WHEN sync_status='CONFLICT_REVIEW' "
                          "THEN sync_status ELSE 'SYNCED' END WHERE record_id=?", (ev["record_id"],))
            s = c.execute("SELECT * FROM sync_sessions WHERE drained_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            if s:
                seq = loads(s["delivery_sequence"], [])
                if ev["replay_count"] == 0 or ev["event_id"] not in [x["eventId"] for x in seq]:
                    seq.append({"eventId": ev["event_id"], "recordId": ev["record_id"], "priority": ev["priority"],
                                "sequenceNumber": ev["sequence_number"], "ackedAt": ts, "outcome": central.get("outcome")})
                c.execute("UPDATE sync_sessions SET delivered=delivered+1, delivery_sequence=? WHERE id=?", (dumps(seq), s["id"]))
                if not c.execute("SELECT 1 FROM outbound_events WHERE status IN ('PENDING','SENDING','ACCEPTED')").fetchone():
                    c.execute("UPDATE sync_sessions SET drained_at=? WHERE id=?", (ts, s["id"]))
        return order

    def fail(self, ev, reason):
        with self.db.tx() as c:
            c.execute("UPDATE outbound_events SET status='FAILED', last_error=?, updated_at=? WHERE event_id=?",
                      (reason, now_iso(), ev["event_id"]))
            c.execute("UPDATE operational_records SET sync_status='FAILED' WHERE record_id=?", (ev["record_id"],))
            c.execute("UPDATE sync_sessions SET failed=failed+1 WHERE id=(SELECT MAX(id) FROM sync_sessions WHERE drained_at IS NULL)")

    def replay(self, eid):
        with self.db.tx() as c:
            r = c.execute("SELECT status FROM outbound_events WHERE event_id=?", (eid,)).fetchone()
            if not r:
                return 404, {"error": "event not found"}
            if r["status"] not in ("ACKNOWLEDGED", "FAILED"):
                return 409, {"error": "event is %s; only ACKNOWLEDGED/FAILED events can be replayed" % r["status"]}
            c.execute("""UPDATE outbound_events SET status='PENDING', replay_count=replay_count+1, next_attempt_at=0,
                updated_at=? WHERE event_id=?""", (now_iso(), eid))
        return 200, {"eventId": eid, "status": "PENDING", "note": "same eventId will be re-sent (duplicate test)"}

    def queue_counts(self):
        r = self.db.q1("""SELECT SUM(status='PENDING') pending, SUM(status='SENDING') inFlight, SUM(status='ACCEPTED') awaitingAck,
            SUM(status='ACKNOWLEDGED') acknowledged, SUM(status='FAILED') failed, COUNT(*) total,
            SUM(status IN ('PENDING','SENDING','ACCEPTED') AND priority='CRITICAL') openCritical,
            SUM(status IN ('PENDING','SENDING','ACCEPTED') AND priority='NORMAL') openNormal,
            SUM(status IN ('PENDING','SENDING','ACCEPTED') AND priority='LOW') openLow FROM outbound_events""")
        return {k: (v or 0) for k, v in r.items()}

    def events(self, status=None, limit=300):
        sql = "SELECT * FROM outbound_events" + (" WHERE status=?" if status else "") + " ORDER BY sequence_number DESC LIMIT ?"
        rows = self.db.q(sql, ((status,) if status else ()) + (limit,))
        out = []
        for r in rows:
            out.append({"eventId": r["event_id"], "recordId": r["record_id"], "recordType": r["record_type"],
                        "eventType": r["event_type"], "priority": r["priority"], "sequenceNumber": r["sequence_number"],
                        "version": r["version"], "createdAt": r["created_at"], "status": r["status"],
                        "retryCount": max(0, r["attempts"] - 1), "attempts": r["attempts"], "acceptedAt": r["accepted_at"],
                        "ackedAt": r["acked_at"], "deliveryOrder": r["delivery_order"], "lastError": r["last_error"],
                        "centralOutcome": r["central_outcome"], "transactionId": r["transaction_id"],
                        "mqQueue": r["mq_queue"], "replayCount": r["replay_count"]})
        return out

    def event_detail(self, eid):
        r = self.db.q1("SELECT * FROM outbound_events WHERE event_id=?", (eid,))
        if not r:
            return None
        r["event"] = loads(r.pop("event_json"))
        r["attemptsLog"] = self.db.q("SELECT * FROM delivery_attempts WHERE event_id=? ORDER BY id", (eid,))
        return r

    # ------------------------------------------------------------------ link observation / sync sessions
    def link_down(self):
        if self.get_state("link_observed", "UP") != "DOWN":
            with self.db.tx() as c:
                self.set_state("link_observed", "DOWN", c)
                self.set_state("offline_since", now_iso(), c)
            return True
        return False

    def link_up(self):
        if self.get_state("link_observed", "UP") == "DOWN":
            since = self.get_state("offline_since")
            from common.util import seconds_since
            with self.db.tx() as c:
                backlog = c.execute("SELECT COUNT(*) FROM outbound_events WHERE status IN ('PENDING','SENDING','ACCEPTED')").fetchone()[0]
                c.execute("""INSERT INTO sync_sessions(offline_since,reconnected_at,offline_duration_sec,backlog_at_start)
                    VALUES(?,?,?,?)""", (since, now_iso(), seconds_since(since), backlog))
                self.set_state("link_observed", "UP", c)
                self.set_state("offline_since", None, c)
                self.set_state("last_reconnect_at", now_iso(), c)
            return True
        return False

    def sync_view(self):
        sessions = self.db.q("SELECT * FROM sync_sessions ORDER BY id DESC LIMIT 20")
        for s in sessions:
            s["delivery_sequence"] = loads(s["delivery_sequence"], [])
        recent = self.db.q("""SELECT event_id, record_id, priority, sequence_number, delivery_order, acked_at, central_outcome
            FROM outbound_events WHERE delivery_order IS NOT NULL ORDER BY delivery_order DESC LIMIT 40""")
        att = self.db.q1("SELECT SUM(result='OK') ok, SUM(result!='OK') failed FROM delivery_attempts WHERE phase='SEND'")
        return {"linkObserved": self.get_state("link_observed", "UP"), "offlineSince": self.get_state("offline_since"),
                "lastSyncAt": self.get_state("last_sync_at"), "lastReconnectAt": self.get_state("last_reconnect_at"),
                "queue": self.queue_counts(), "sessions": sessions, "recentDeliveries": recent,
                "sendAttempts": {"ok": (att or {}).get("ok") or 0, "failed": (att or {}).get("failed") or 0}}

    # ------------------------------------------------------------------ demo data
    def generate(self, count, actor, priorities=None):
        out = []
        for i in range(count):
            rtype = random.choice(["PR", "GRN", "MAINTENANCE", "EMERGENCY_MAINTENANCE"]) if not priorities else None
            if priorities:
                prio = priorities[i % len(priorities)]
                rtype = {"CRITICAL": "EMERGENCY_MAINTENANCE", "NORMAL": random.choice(["PR", "GRN"]),
                         "LOW": "MAINTENANCE"}[prio]
            st, body = self.create_record({"recordType": rtype, "payload": sample_payload(rtype, self.vessel_id)}, actor)
            out.append(body)
        return out


ITEMS = [("Hydraulic Pump", "EA"), ("Fuel Injector Nozzle", "EA"), ("Mooring Rope 64mm", "M"), ("Lube Oil SAE40", "L"),
         ("Air Filter Element", "EA"), ("Fire Hose 45mm", "EA"), ("Gasket Kit - Main Engine", "SET"), ("Life Jacket SOLAS", "EA")]
EQUIP = ["Main Engine", "Aux Generator No.2", "Ballast Pump", "Deck Crane", "Fire Pump", "Bow Thruster", "Watermaker"]


def sample_payload(rtype, vessel):
    if rtype == "PR":
        item, uom = random.choice(ITEMS)
        return {"item": item, "quantity": random.randint(1, 12), "uom": uom, "requiredBy": "2026-12-%02d" % random.randint(1, 28),
                "justification": "Onboard stock below minimum", "status": "SUBMITTED"}
    if rtype == "GRN":
        item, uom = random.choice(ITEMS)
        return {"poNumber": "PO-%d" % random.randint(70000, 79999), "item": item, "receivedQuantity": random.randint(1, 10),
                "uom": uom, "receivedBy": "Chief Officer", "condition": "GOOD", "status": "RECEIVED"}
    if rtype == "MAINTENANCE":
        return {"equipment": random.choice(EQUIP), "task": "Planned maintenance - 500h service",
                "runningHours": random.randint(8000, 20000), "status": "IN_PROGRESS", "workPerformed": "", "technicianRemarks": ""}
    return {"equipment": random.choice(EQUIP), "fault": random.choice(["High vibration", "Oil leak", "Overheating", "Loss of pressure"]),
            "safetyImpact": random.choice(["HIGH", "MEDIUM"]), "status": "OPEN", "observedAt": now_iso(),
            "technicianRemarks": "Equipment isolated pending repair"}
