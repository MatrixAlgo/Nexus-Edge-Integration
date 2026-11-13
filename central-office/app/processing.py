"""Central business processing of vessel events: validation, idempotency, three-way merge, conflicts.

Transaction boundary: everything for one event (ledger row, business transaction, record + version history,
conflict, downlink entry, audit) commits in ONE SQLite transaction (BEGIN IMMEDIATE ... COMMIT, synchronous=FULL).
The ledger row keyed by eventId is the idempotency guard: a second delivery of the same eventId changes no
business data; it only increments received_count and is logged in duplicate_log.
"""
import re

from common.util import PRIORITY_RANK, RECORD_TYPES, dumps, loads, new_id, now_iso

UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EVENT_TYPES = ("RECORD_CREATED", "RECORD_UPDATED")


def validate_event(e):
    errs = []
    if not isinstance(e, dict):
        return ["event must be a JSON object"]
    if not UUID_RX.match(str(e.get("eventId", ""))):
        errs.append("eventId must be a lowercase UUID")
    for f in ("recordId", "vesselId", "sourceSystem", "createdAt", "updatedAt", "updatedBy"):
        if not isinstance(e.get(f), str) or not e.get(f):
            errs.append("%s is required" % f)
    if e.get("recordType") not in RECORD_TYPES:
        errs.append("recordType must be one of %s" % ", ".join(RECORD_TYPES))
    if e.get("priority") not in PRIORITY_RANK:
        errs.append("priority must be CRITICAL, NORMAL or LOW")
    if e.get("eventType") not in EVENT_TYPES:
        errs.append("eventType must be RECORD_CREATED or RECORD_UPDATED")
    for f in ("sequenceNumber", "version"):
        if not isinstance(e.get(f), int) or e.get(f) < 1:
            errs.append("%s must be a positive integer" % f)
    if not isinstance(e.get("payload"), dict):
        errs.append("payload must be an object")
    if e.get("eventType") == "RECORD_UPDATED":
        if not isinstance(e.get("baseVersion"), int):
            errs.append("baseVersion is required for RECORD_UPDATED")
        if not isinstance(e.get("baseSnapshot"), dict):
            errs.append("baseSnapshot is required for RECORD_UPDATED")
        if not isinstance(e.get("changedFields"), dict):
            errs.append("changedFields is required for RECORD_UPDATED")
    return errs


def three_way_merge(base, office, vessel_changes, policy):
    """Merge a vessel change-set into the current office record using the common base snapshot.

    Returns (merged_payload, decisions, manual_fields). Never last-write-wins: a field changed on both sides
    to different values is decided by the ownership rule, or held for manual review.
    """
    ownership = policy.get("ownership", {})
    manual = set(policy.get("manualReview", []))
    merged = dict(office)
    decisions, manual_fields = [], []
    office_changed = {f for f in set(base) | set(office) if office.get(f) != base.get(f)}
    for f in sorted(vessel_changes):
        v = vessel_changes[f]
        if base.get(f) == v:
            continue
        d = {"field": f, "base": base.get(f), "office": office.get(f), "vessel": v}
        if f not in office_changed:
            merged[f] = v
            d.update(decision="AUTO_MERGE", winner="VESSEL", rule="changed on vessel only")
        elif office.get(f) == v:
            d.update(decision="IDENTICAL", winner="BOTH", rule="both sides made the same change")
        elif f in manual:
            d.update(decision="MANUAL_REVIEW", winner=None, rule="critical field - manual review required")
            manual_fields.append(f)
        elif ownership.get(f) == "VESSEL":
            merged[f] = v
            d.update(decision="OWNER_RULE", winner="VESSEL", rule="field owned by vessel")
        elif ownership.get(f) == "OFFICE":
            d.update(decision="OWNER_RULE", winner="OFFICE", rule="field owned by office")
        else:
            d.update(decision="MANUAL_REVIEW", winner=None, rule="no owner defined - manual review required")
            manual_fields.append(f)
        decisions.append(d)
    for f in sorted(office_changed - set(vessel_changes)):
        decisions.append({"field": f, "base": base.get(f), "office": office.get(f), "vessel": base.get(f),
                          "decision": "AUTO_MERGE", "winner": "OFFICE", "rule": "changed in office only"})
    return merged, decisions, manual_fields


def audit(c, actor, action, entity, entity_id, detail=None):
    c.execute("INSERT INTO audit_log(ts,actor,action,entity,entity_id,detail) VALUES(?,?,?,?,?,?)",
              (now_iso(), actor, action, entity, entity_id, dumps(detail) if detail is not None else None))


def next_counter(c, name):
    c.execute("INSERT INTO counters(name,value) VALUES(?,1) ON CONFLICT(name) DO UPDATE SET value=value+1", (name,))
    return c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0]


def _ledger_view(r):
    return {"eventId": r["event_id"], "status": r["status"], "outcome": r["outcome"], "detail": loads(r["detail"]),
            "transactionId": r["transaction_id"], "processedAt": r["processed_at"],
            "receivedCount": r["received_count"], "ackRecorded": r["ack_recorded_at"] is not None,
            "ackRecordedAt": r["ack_recorded_at"], "processingOrder": r["processing_order"]}


def process_event(db, policies, e, mq):
    """Returns (http_status, body). 200 = business result committed (PROCESSED or DUPLICATE); 422 = rejected."""
    mq = mq or {}
    ts = now_iso()
    eid = str(e.get("eventId", "")) if isinstance(e, dict) else ""
    with db.tx() as c:
        if eid:
            row = c.execute("SELECT * FROM event_ledger WHERE event_id=?", (eid,)).fetchone()
            if row:
                c.execute("UPDATE event_ledger SET received_count=received_count+1, last_received_at=? WHERE event_id=?",
                          (ts, eid))
                c.execute("INSERT INTO duplicate_log(event_id,vessel_id,received_at,mq_queue,note) VALUES(?,?,?,?,?)",
                          (eid, row["vessel_id"], ts, mq.get("queue"), "duplicate delivery - no business change"))
                audit(c, "central-processor", "DUPLICATE_IGNORED", "event", eid, {"queue": mq.get("queue")})
                row = c.execute("SELECT * FROM event_ledger WHERE event_id=?", (eid,)).fetchone()
                body = _ledger_view(row)
                body.update(result="DUPLICATE", duplicate=True)
                return 200, body
        errs = validate_event(e)
        if errs:
            if UUID_RX.match(eid):
                c.execute("""INSERT INTO event_ledger(event_id,record_id,vessel_id,record_type,event_type,priority,
                    sequence_number,event_created_at,status,outcome,detail,mq_queue,mq_backout_count,first_received_at,
                    last_received_at,processed_at) VALUES(?,?,?,?,?,?,?,?,'REJECTED','VALIDATION_FAILED',?,?,?,?,?,?)""",
                          (eid, str(e.get("recordId")), str(e.get("vesselId")), str(e.get("recordType")),
                           str(e.get("eventType")), str(e.get("priority")), int(e.get("sequenceNumber") or 0)
                           if isinstance(e.get("sequenceNumber"), int) else 0, str(e.get("createdAt")),
                           dumps(errs), mq.get("queue"), mq.get("backoutCount"), ts, ts, ts))
                audit(c, "central-processor", "EVENT_REJECTED", "event", eid, errs)
            return 422, {"eventId": eid, "result": "REJECTED", "status": "REJECTED", "errors": errs}

        rid, vessel = e["recordId"], e["vesselId"]
        cur = c.execute("SELECT * FROM records WHERE record_id=?", (rid,)).fetchone()
        outcome, detail, version_after, conflict_id = None, {}, None, None

        def write_record(payload, version, changed_by, source, insert):
            if insert:
                c.execute("""INSERT INTO records(record_id,record_type,vessel_id,priority,version,payload,source_system,
                    created_at,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                          (rid, e["recordType"], vessel, e["priority"], version, dumps(payload), e["sourceSystem"],
                           e["createdAt"], e["updatedAt"], changed_by))
            else:
                c.execute("UPDATE records SET version=?, payload=?, updated_at=?, updated_by=?, priority=? WHERE record_id=?",
                          (version, dumps(payload), ts, changed_by, e["priority"], rid))
            c.execute("""INSERT OR REPLACE INTO record_versions(record_id,version,payload,changed_by,change_source,event_id,
                created_at) VALUES(?,?,?,?,?,?,?)""", (rid, version, dumps(payload), changed_by, source, eid, ts))

        if cur is None:
            # first sight of the record (create, or an update whose create has not arrived yet)
            write_record(e["payload"], e["version"], e["updatedBy"], "VESSEL_SYNC", True)
            outcome, version_after = "CREATED", e["version"]
        elif e["eventType"] == "RECORD_CREATED" or e["version"] <= 0:
            outcome, version_after = "SUPERSEDED", cur["version"]
            detail = {"note": "record already exists centrally at version %d; create event recorded, no change" % cur["version"]}
        else:
            office = loads(cur["payload"], {})
            base = e["baseSnapshot"]
            if cur["version"] == e["baseVersion"] and office == base:
                new_payload = dict(office)
                new_payload.update(e["changedFields"])
                version_after = cur["version"] + 1
                write_record(new_payload, version_after, e["updatedBy"], "VESSEL_SYNC", False)
                outcome = "UPDATED"
            else:
                policy = policies.get(e["recordType"], {})
                merged, decisions, manual = three_way_merge(base, office, e["changedFields"], policy)
                vessel_payload = dict(base)
                vessel_payload.update(e["changedFields"])
                version_after = cur["version"] + 1
                conflict_id = "CNF-%04d" % next_counter(c, "conflict")
                status = "OPEN" if manual else "AUTO_RESOLVED"
                write_record(merged, version_after, "MERGE(%s+OFFICE)" % e["updatedBy"], "SYNC_MERGE", False)
                c.execute("UPDATE records SET conflict_open=? WHERE record_id=?", (1 if manual else 0, rid))
                c.execute("""INSERT INTO conflicts(conflict_id,record_id,vessel_id,event_id,detected_at,status,base_version,
                    office_version,vessel_version,base_payload,office_payload,vessel_payload,merged_payload,field_decisions,
                    final_payload,final_version,resolved_at,resolved_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (conflict_id, rid, vessel, eid, ts, status, e["baseVersion"], cur["version"], e["version"],
                           dumps(base), dumps(office), dumps(vessel_payload), dumps(merged), dumps(decisions),
                           None if manual else dumps(merged), None if manual else version_after,
                           None if manual else ts, None if manual else "policy-engine"))
                audit(c, "policy-engine", "CONFLICT_DETECTED", "record", rid,
                      {"conflictId": conflict_id, "status": status, "manualFields": manual})
                outcome = "CONFLICT_MANUAL_REVIEW" if manual else "MERGED"
                detail = {"conflictId": conflict_id, "manualFields": manual}
                # send the merged record back to the vessel so both sides converge
                c.execute("INSERT INTO downlink_feed(vessel_id,record_id,version,payload,reason,created_at) VALUES(?,?,?,?,?,?)",
                          (vessel, rid, version_after, dumps(merged), "SYNC_MERGE", ts))

        txid = "TX-%06d" % next_counter(c, "transaction")
        order = next_counter(c, "processing_order")
        c.execute("""INSERT INTO event_ledger(event_id,record_id,vessel_id,record_type,event_type,priority,sequence_number,
            event_created_at,status,outcome,detail,transaction_id,mq_queue,mq_backout_count,first_received_at,
            last_received_at,processed_at,processing_order) VALUES(?,?,?,?,?,?,?,?,'PROCESSED',?,?,?,?,?,?,?,?,?)""",
                  (eid, rid, vessel, e["recordType"], e["eventType"], e["priority"], e["sequenceNumber"], e["createdAt"],
                   outcome, dumps(detail), txid, mq.get("queue"), mq.get("backoutCount"), ts, ts, ts, order))
        c.execute("""INSERT INTO business_transactions(transaction_id,event_id,record_id,vessel_id,action,version_after,
            created_at) VALUES(?,?,?,?,?,?,?)""", (txid, eid, rid, vessel, outcome, version_after, ts))
        audit(c, "central-processor", "EVENT_PROCESSED", "event", eid,
              {"recordId": rid, "outcome": outcome, "transactionId": txid, "queue": mq.get("queue")})
        row = c.execute("SELECT * FROM event_ledger WHERE event_id=?", (eid,)).fetchone()
    body = _ledger_view(row)
    body.update(result="PROCESSED", duplicate=False, recordId=rid, vesselId=vessel, versionAfter=version_after)
    return 200, body


def record_ack(db, ack):
    """Called by ACE flow D5_AckDispatcher for each message consumed from D5.OUT.ACK."""
    eid = str(ack.get("eventId", ""))
    with db.tx() as c:
        row = c.execute("SELECT event_id FROM event_ledger WHERE event_id=?", (eid,)).fetchone()
        if not row:
            return 404, {"error": "unknown eventId", "eventId": eid}
        c.execute("""UPDATE event_ledger SET ack_recorded_at=COALESCE(ack_recorded_at, ?), ack_count=ack_count+1
            WHERE event_id=?""", (now_iso(), eid))
    return 200, {"eventId": eid, "ackRecorded": True}


def event_status(db, eid):
    row = db.q1("SELECT * FROM event_ledger WHERE event_id=?", (eid,))
    if not row:
        return {"eventId": eid, "status": "UNKNOWN", "ackRecorded": False}
    return _ledger_view(row)


def resolve_conflict(db, conflict_id, decisions, actor, note):
    """decisions: {field: 'VESSEL'|'OFFICE'|{'custom': value}} for every MANUAL_REVIEW field."""
    with db.tx() as c:
        cf = c.execute("SELECT * FROM conflicts WHERE conflict_id=?", (conflict_id,)).fetchone()
        if not cf:
            return 404, {"error": "conflict not found"}
        if cf["status"] != "OPEN":
            return 409, {"error": "conflict is %s" % cf["status"]}
        fd = loads(cf["field_decisions"], [])
        pending = [d for d in fd if d["decision"] == "MANUAL_REVIEW"]
        missing = [d["field"] for d in pending if d["field"] not in decisions]
        if missing:
            return 400, {"error": "decision required for: %s" % ", ".join(missing)}
        rec = c.execute("SELECT * FROM records WHERE record_id=?", (cf["record_id"],)).fetchone()
        final = loads(rec["payload"], {})
        for d in pending:
            choice = decisions[d["field"]]
            if choice == "VESSEL":
                final[d["field"]], d["winner"] = d["vessel"], "VESSEL"
            elif choice == "OFFICE":
                final[d["field"]], d["winner"] = d["office"], "OFFICE"
            elif isinstance(choice, dict) and "custom" in choice:
                final[d["field"]], d["winner"] = choice["custom"], "CUSTOM"
            else:
                return 400, {"error": "invalid decision for %s" % d["field"]}
            d["resolvedBy"], d["decision"] = actor, "MANUAL_RESOLVED"
        ts = now_iso()
        ver = rec["version"] + 1
        c.execute("UPDATE records SET payload=?, version=?, updated_at=?, updated_by=?, conflict_open=0 WHERE record_id=?",
                  (dumps(final), ver, ts, actor, cf["record_id"]))
        c.execute("""INSERT INTO record_versions(record_id,version,payload,changed_by,change_source,event_id,created_at)
            VALUES(?,?,?,?,'CONFLICT_RESOLUTION',?,?)""", (cf["record_id"], ver, dumps(final), actor, cf["event_id"], ts))
        c.execute("""UPDATE conflicts SET status='RESOLVED', field_decisions=?, final_payload=?, final_version=?, resolved_at=?,
            resolved_by=?, resolution_note=? WHERE conflict_id=?""",
                  (dumps(fd), dumps(final), ver, ts, actor, note, conflict_id))
        c.execute("INSERT INTO downlink_feed(vessel_id,record_id,version,payload,reason,created_at) VALUES(?,?,?,?,?,?)",
                  (cf["vessel_id"], cf["record_id"], ver, dumps(final), "CONFLICT_RESOLVED", ts))
        audit(c, actor, "CONFLICT_RESOLVED", "record", cf["record_id"],
              {"conflictId": conflict_id, "decisions": decisions, "note": note, "finalVersion": ver})
    return 200, {"conflictId": conflict_id, "status": "RESOLVED", "finalVersion": ver, "finalRecord": final}


def office_upsert(db, rec, actor):
    """Office AMOS create/update (central side). Changes flow to the owning vessel via the downlink feed."""
    ts = now_iso()
    rid = rec.get("recordId")
    if not rid:
        return 400, {"error": "recordId required"}
    with db.tx() as c:
        cur = c.execute("SELECT * FROM records WHERE record_id=?", (rid,)).fetchone()
        if cur is None:
            for f in ("recordType", "vesselId", "priority"):
                if not rec.get(f):
                    return 400, {"error": "%s required" % f}
            if rec["recordType"] not in RECORD_TYPES or rec["priority"] not in PRIORITY_RANK:
                return 400, {"error": "invalid recordType or priority"}
            payload, ver = rec.get("payload") or {}, 1
            c.execute("""INSERT INTO records(record_id,record_type,vessel_id,priority,version,payload,source_system,created_at,
                updated_at,updated_by) VALUES(?,?,?,?,?,?,'OFFICE_AMOS',?,?,?)""",
                      (rid, rec["recordType"], rec["vesselId"], rec["priority"], ver, dumps(payload), ts, ts, actor))
            vessel, action = rec["vesselId"], "OFFICE_CREATED"
        else:
            payload = loads(cur["payload"], {})
            changes = rec.get("changes") or rec.get("payload") or {}
            if not changes:
                return 400, {"error": "no changes supplied"}
            payload.update(changes)
            ver = cur["version"] + 1
            c.execute("UPDATE records SET payload=?, version=?, updated_at=?, updated_by=?, priority=COALESCE(?,priority) WHERE record_id=?",
                      (dumps(payload), ver, ts, actor, rec.get("priority"), rid))
            vessel, action = cur["vessel_id"], "OFFICE_UPDATED"
        c.execute("""INSERT INTO record_versions(record_id,version,payload,changed_by,change_source,event_id,created_at)
            VALUES(?,?,?,?,'OFFICE_AMOS',NULL,?)""", (rid, ver, dumps(payload), actor, ts))
        c.execute("INSERT INTO downlink_feed(vessel_id,record_id,version,payload,reason,created_at) VALUES(?,?,?,?,?,?)",
                  (vessel, rid, ver, dumps(payload), action, ts))
        audit(c, actor, action, "record", rid, {"version": ver})
    return 200, {"recordId": rid, "version": ver, "payload": payload}
