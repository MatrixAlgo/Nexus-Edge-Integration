#!/usr/bin/env python3
"""D5 end-to-end + reliability test runner against the LIVE stack (IBM ACE IS_D5_EDGE, IBM MQ D5QM, central, edges).

    sudo python3 tests/end-to-end/run_scenarios.py            (root: uses systemctl and runmqsc for R-01/R-02)

Writes tests/reports/test-report.md and one JSON evidence file per test in tests/reports/evidence/.
A test is only reported PASS if every assertion in it executed and held.
"""
import datetime, json, os, re, subprocess, sys, time, traceback, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORT_DIR = os.path.join(ROOT, "tests", "reports")
EVID = os.path.join(REPORT_DIR, "evidence")
os.makedirs(EVID, exist_ok=True)
C, NET, ACE = "http://127.0.0.1:8500", "http://127.0.0.1:8510", "http://127.0.0.1:7840"
EDGE = {"VESSEL-01": "http://127.0.0.1:8501", "VESSEL-02": "http://127.0.0.1:8502"}
RANK = {"CRITICAL": 0, "NORMAL": 1, "LOW": 2}
RESULTS, CTX = [], {}
sys.path.insert(0, ROOT)
from common.edgeconfig import DEFAULT_CONFIG  # noqa: E402  (single source of the factory priority rules)

DEFAULT_PRIORITY_RULES = DEFAULT_CONFIG["priorityRules"]


def api(method, url, body=None, headers=None, timeout=15):
    h = {"Content-Type": "application/json", "X-D5-User": "e2e-test"}
    h.update(headers or {})
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode()


def ok(method, url, body=None, **kw):
    st, b = api(method, url, body, **kw)
    assert 200 <= st < 300, "%s %s -> HTTP %s %s" % (method, url, st, b)
    return b


def wait(fn, timeout, what, interval=1.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except (AssertionError, OSError, urllib.error.URLError):
            pass
        time.sleep(interval)
    raise AssertionError("timed out after %ss waiting for %s (last=%r)" % (timeout, what, last))


def link(v, **kw):
    return ok("POST", NET + "/links/" + v, kw)


def status(v):
    return ok("GET", EDGE[v] + "/api/status")


def edge_event(v, eid):
    return ok("GET", "%s/api/events/%s" % (EDGE[v], eid))


def drained(v):
    q = status(v)["queue"]
    return q["pending"] + q["inFlight"] + q["awaitingAck"] == 0


def priority_rules(v):
    return ok("GET", EDGE[v] + "/api/config")["active"]["priorityRules"]


def ensure_default_priority_rules():
    """Make the scenarios deterministic: every vessel must run the factory priority rules (PR/GRN=NORMAL, MAINTENANCE=LOW,
    EMERGENCY_MAINTENANCE=CRITICAL) before the first test, otherwise a rule left over from manual testing (e.g. PR=CRITICAL)
    changes the CRITICAL/NORMAL/LOW split of D5-02/D5-03. Publishes a configuration version ONLY if a vessel differs."""
    before = {v: priority_rules(v) for v in EDGE}
    if all(r == DEFAULT_PRIORITY_RULES for r in before.values()):
        print("precondition: default priority rules already active on %s" % ", ".join(EDGE))
        return None
    r = ok("POST", C + "/api/config/publish", {"target": {"vessels": list(EDGE)},
           "notes": "e2e: default priority rules before the scenarios", "config": {"priorityRules": DEFAULT_PRIORITY_RULES}})
    wait(lambda: all(priority_rules(v) == DEFAULT_PRIORITY_RULES for v in EDGE), 60, "default priority rules applied on all vessels")
    print("precondition: published configuration v%s to restore the default priority rules (was %s)" % (r["configVersion"], before))
    return r["configVersion"]


def mq_depths():
    p = subprocess.run(["su", "mqm", "-s", "/bin/bash", "-c", "/opt/mqm/bin/runmqsc -e D5QM"],
                       input="DISPLAY QLOCAL('D5.*') CURDEPTH\n", capture_output=True, text=True, timeout=20)
    return {q: int(d) for q, d in re.findall(r"QUEUE\(([^)]+)\)\s+TYPE\(QLOCAL\)\s+CURDEPTH\((\d+)\)", p.stdout)}


def ledger(eid):
    return ok("GET", C + "/internal/event-status?eventId=" + eid)


def test(tid, name, expected):
    def deco(fn):
        def run():
            t0 = time.time()
            ev = {"id": tid, "name": name, "startedAt": datetime.datetime.utcnow().isoformat() + "Z"}
            try:
                actual = fn(ev)
                passed = True
            except Exception as e:  # noqa
                actual, passed = "FAILED: %s" % e, False
                ev["traceback"] = traceback.format_exc()
            ev.update(durationSec=round(time.time() - t0, 1), passed=passed, actual=actual)
            path = os.path.join(EVID, "%s.json" % tid)
            with open(path, "w") as f:
                json.dump(ev, f, indent=2, default=str)
            RESULTS.append({"id": tid, "name": name, "expected": expected, "actual": actual, "passed": passed,
                            "evidence": os.path.relpath(path, ROOT), "sec": ev["durationSec"]})
            print("%-6s %-4s %s  (%.0fs)\n        %s" % (tid, "PASS" if passed else "FAIL", name, ev["durationSec"], actual), flush=True)
        run.tid = tid
        return run
    return deco


# ---------------------------------------------------------------------------------------------- scenarios
@test("D5-01", "Normal connectivity: PR onboard -> ACE REST -> MQ -> central -> business ack",
      "Event accepted by ACE (HTTP 202, persistent put to D5.IN.NORMAL), processed centrally (1 business transaction), "
      "ack recorded via D5.OUT.ACK, edge status ACKNOWLEDGED, record visible in Office AMOS")
def d5_01(ev):
    link("VESSEL-01", state="CONNECTED")
    r = ok("POST", EDGE["VESSEL-01"] + "/api/records", {"recordType": "PR", "priority": "NORMAL",
           "payload": {"item": "Hydraulic Pump", "quantity": 2, "uom": "EA", "status": "SUBMITTED", "requiredBy": "2026-12-15"}})
    eid, rid = r["eventId"], r["recordId"]
    CTX["d501"] = (eid, rid)
    e = wait(lambda: (lambda x: x if x["status"] == "ACKNOWLEDGED" else None)(edge_event("VESSEL-01", eid)), 60, "ACKNOWLEDGED")
    send = [a for a in e["attemptsLog"] if a["phase"] == "SEND" and a["result"] == "OK"][0]
    ace = json.loads(send["detail"])
    led = ledger(eid)
    rec = ok("GET", C + "/api/central/records/" + rid)
    ev.update(edgeEvent=e, aceResponse=ace, ledger=led, centralRecord=rec)
    assert send["http_status"] == 202 and ace["status"] == "ACCEPTED" and ace["queue"] == "D5.IN.NORMAL" and ace["mqPriority"] == 5
    assert ace["mqMsgId"].startswith("X'414d5120") and "NOT a business acknowledgement" in ace["note"]
    assert led["status"] == "PROCESSED" and led["ackRecorded"] and led["outcome"] == "CREATED"
    assert rec["payload"]["item"] == "Hydraulic Pump" and rec["version"] == 1 and len(rec["transactions"]) == 1
    return ("%s %s ACKNOWLEDGED; ACE 202 queue=%s mqMsgId=%s...; central %s %s; ack recorded at %s"
            % (rid, eid[:8], ace["queue"], ace["mqMsgId"][:20], led["transactionId"], led["outcome"], led["ackRecordedAt"]))


OFFLINE_MIX = ["LOW", "NORMAL", "LOW", "CRITICAL", "NORMAL", "LOW", "CRITICAL", "NORMAL", "CRITICAL", "LOW"]


@test("D5-02", "Satellite disconnection: 10 records created offline",
      "Link DISCONNECTED; all 10 records committed in vessel SQLite; 10 events pending; relay blocks the requests; "
      "office has none of them; no D5 message reaches MQ")
def d5_02(ev):
    link("VESSEL-01", state="DISCONNECTED")
    blocked0 = ok("GET", NET + "/links/VESSEL-01")["stats"]["blocked"]
    depth0 = mq_depths()
    created = ok("POST", EDGE["VESSEL-01"] + "/api/demo/generate", {"count": 10, "priorities": OFFLINE_MIX})
    CTX["d502"] = created
    time.sleep(8)
    st = status("VESSEL-01")
    ids = [c["recordId"] for c in created]
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "vessel-simulator/vessel-01/data/edge.db"), uri=True)
    in_sqlite = con.execute("SELECT COUNT(*) FROM operational_records WHERE record_id IN (%s)" % ",".join("?" * 10), ids).fetchone()[0]
    pend = con.execute("SELECT COUNT(*) FROM outbound_events WHERE status='PENDING' AND event_id IN (%s)" % ",".join("?" * 10),
                       [c["eventId"] for c in created]).fetchone()[0]
    con.close()
    at_central = [rid for rid in ids if api("GET", C + "/api/central/records/" + rid)[0] == 200]
    blocked1 = ok("GET", NET + "/links/VESSEL-01")["stats"]["blocked"]
    depth1 = mq_depths()
    ev.update(created=created, edgeStatus=st, sqliteRows=in_sqlite, pendingInSqlite=pend, atCentral=at_central,
              relayBlocked=[blocked0, blocked1], mqBefore=depth0, mqAfter=depth1)
    assert st["linkObserved"] == "DOWN"
    assert in_sqlite == 10 and pend == 10 and st["queue"]["pending"] >= 10
    assert at_central == []
    assert blocked1 > blocked0
    assert sum(depth1.get(q, 0) for q in ("D5.IN.CRITICAL", "D5.IN.NORMAL", "D5.IN.LOW")) == 0
    return ("10 records in vessel SQLite, %d pending in outbox (queue depth %d); edge sees link DOWN; relay blocked %d requests; "
            "0 of 10 at central" % (pend, st["queue"]["pending"], blocked1 - blocked0))


@test("D5-03", "Reconnection and priority order",
      "Automatic resume without user action; the 3 CRITICAL events are delivered first, then 3 NORMAL, then 4 LOW (FIFO within "
      "priority); all 10 acknowledged and present in the central ledger")
def d5_03(ev):
    created = CTX["d502"]
    link("VESSEL-01", state="CONNECTED")
    wait(lambda: drained("VESSEL-01"), 180, "VESSEL-01 drained", 2)
    ids = {c["eventId"]: c for c in created}
    evs = [edge_event("VESSEL-01", e) for e in ids]
    evs.sort(key=lambda e: e["delivery_order"])
    order = [e["priority"] for e in evs]
    leds = {e: ledger(e) for e in ids}
    proc = [ids[e]["recordId"] for e, l in sorted(leds.items(), key=lambda kv: kv[1]["processingOrder"])]
    sess = ok("GET", EDGE["VESSEL-01"] + "/api/sync")["sessions"][0]
    ev.update(deliveryOrder=[(e["record_id"], e["priority"], e["sequence_number"], e["delivery_order"]) for e in evs],
              centralProcessingOrder=proc, session=sess, ledger=leds)
    assert all(e["status"] == "ACKNOWLEDGED" for e in evs)
    assert all(l["status"] == "PROCESSED" and l["ackRecorded"] for l in leds.values())
    assert order == sorted(order, key=lambda p: RANK[p]), order
    assert order == ["CRITICAL"] * 3 + ["NORMAL"] * 3 + ["LOW"] * 4, order  # OFFLINE_MIX under the default priority rules
    for p in RANK:
        seqs = [e["sequence_number"] for e in evs if e["priority"] == p]
        assert seqs == sorted(seqs), "FIFO broken in %s" % p
    assert proc == [e["record_id"] for e in evs], "central processing order differs from delivery order"
    return "delivered %s; central processing order identical; session backlog %s drained in %s" % (
        " > ".join("%s(%s)" % (e["record_id"], e["priority"][0]) for e in evs), sess["backlog_at_start"],
        sess["drained_at"])


@test("D5-04", "Duplicate prevention (replay of an existing eventId)",
      "Edge replay and a raw duplicate POST to ACE are both accepted at transport level; central detects the duplicates; "
      "still exactly 1 business transaction and 1 record version for the event")
def d5_04(ev):
    eid, rid = CTX["d501"]
    before = ledger(eid)
    raw = edge_event("VESSEL-01", eid)["event"]
    ok("POST", EDGE["VESSEL-01"] + "/api/events/%s/replay" % eid)
    st, body = api("POST", ACE + "/d5/v1/events", raw)                  # network-level duplicate straight to ACE
    assert st == 202, (st, body)
    after = wait(lambda: (lambda l: l if l["receivedCount"] >= before["receivedCount"] + 2 else None)(ledger(eid)), 60, "duplicates")
    e = wait(lambda: (lambda x: x if x["status"] == "ACKNOWLEDGED" else None)(edge_event("VESSEL-01", eid)), 60, "re-ack")
    rec = ok("GET", C + "/api/central/records/" + rid)
    dups = [d for d in ok("GET", C + "/api/central/duplicates") if d["event_id"] == eid]
    txs = [t for t in rec["transactions"] if t["event_id"] == eid]
    ev.update(before=before, after=after, record=rec, duplicateLog=dups, edgeEvent=e, aceDuplicateResponse=body)
    assert len(txs) == 1 and txs[0]["transaction_id"] == before["transactionId"]
    assert rec["version"] == 1 and len(rec["versions"]) == 1
    assert len(dups) >= 2 and e["replay_count"] >= 1
    return "eventId %s received %d times centrally; %d duplicate deliveries logged; business transactions for the event: %d (%s)" % (
        eid[:8], after["receivedCount"], len(dups), len(txs), txs[0]["transaction_id"])


@test("D5-05", "Conflict detection and resolution (MNT record updated on vessel and in office while offline)",
      "Conflict detected on reconnect; status -> MANUAL_REVIEW; technicianRemarks -> OWNER_RULE (vessel); one-sided fields "
      "auto-merged; manual resolution recorded with actor; vessel and office converge on the final version; full history kept")
def d5_05(ev):
    v = "VESSEL-01"
    base = {"equipment": "Main Engine", "task": "Cylinder head overhaul - unit 3", "status": "IN_PROGRESS", "runningHours": 12480,
            "workPerformed": "", "technicianRemarks": "", "plannedDate": "2026-11-20", "assignedVendor": "", "officeRemarks": ""}
    r = ok("POST", EDGE[v] + "/api/records", {"recordType": "MAINTENANCE", "priority": "CRITICAL", "payload": base})
    rid = r["recordId"]
    wait(lambda: drained(v), 60, "create synced")
    link(v, state="DISCONNECTED")
    ok("PUT", EDGE[v] + "/api/records/" + rid, {"changes": {"status": "COMPLETED", "runningHours": 12500,
       "workPerformed": "Head overhauled, injectors replaced", "technicianRemarks": "Tested OK at 85% load"}})
    ok("POST", C + "/api/office/records", {"recordId": rid, "changes": {"status": "ON_HOLD", "plannedDate": "2026-12-02",
       "assignedVendor": "Gulf Marine Services", "officeRemarks": "Awaiting OEM spares", "technicianRemarks": "Hold - spares"}},
       headers={"X-D5-User": "technical-superintendent"})
    link(v, state="CONNECTED")
    cf = wait(lambda: [c for c in ok("GET", C + "/api/conflicts") if c["record_id"] == rid], 90, "conflict")[0]
    dec = {d["field"]: d for d in cf["field_decisions"]}
    assert cf["status"] == "OPEN" and dec["status"]["decision"] == "MANUAL_REVIEW"
    assert dec["technicianRemarks"]["decision"] == "OWNER_RULE" and dec["technicianRemarks"]["winner"] == "VESSEL"
    assert dec["runningHours"]["winner"] == "VESSEL" and dec["plannedDate"]["winner"] == "OFFICE"
    res = ok("POST", C + "/api/conflicts/%s/resolve" % cf["conflict_id"],
             {"decisions": {"status": "VESSEL"}, "actor": "technical-superintendent", "note": "Confirmed completion with chief engineer"})
    final = res["finalRecord"]
    onboard = wait(lambda: (lambda x: x if x["version"] == res["finalVersion"] else None)(ok("GET", EDGE[v] + "/api/records/" + rid)),
                   60, "vessel converged")
    hist = ok("GET", C + "/api/central/records/" + rid)
    cf2 = [c for c in ok("GET", C + "/api/conflicts") if c["conflict_id"] == cf["conflict_id"]][0]
    ev.update(conflict=cf, resolution=res, vesselCopy=onboard, history=hist)
    assert final["status"] == "COMPLETED" and final["plannedDate"] == "2026-12-02" and final["runningHours"] == 12500
    assert final["technicianRemarks"] == "Tested OK at 85% load" and final["assignedVendor"] == "Gulf Marine Services"
    assert onboard["payload"] == final and onboard["sync_status"] == "SYNCED"
    assert cf2["status"] == "RESOLVED" and cf2["resolved_by"] == "technical-superintendent"
    assert [x["change_source"] for x in hist["versions"]] == ["VESSEL_SYNC", "OFFICE_AMOS", "SYNC_MERGE", "CONFLICT_RESOLUTION"]
    return "%s on %s: manual=status, owner rule=technicianRemarks(VESSEL), auto-merged %d fields; resolved by %s -> v%d; vessel converged (v%d)" % (
        cf["conflict_id"], rid, sum(1 for d in cf["field_decisions"] if d["decision"] == "AUTO_MERGE"), cf2["resolved_by"],
        res["finalVersion"], onboard["version"])


@test("D5-06a", "Fleet configuration v-next to GULF-FLEET (VESSEL-02 offline during publish)",
      "Online vessel applies at once; offline vessel stays PENDING and applies automatically after reconnect; both report the new "
      "version and the new priority rule is effective")
def d5_06a(ev):
    link("VESSEL-02", state="DISCONNECTED")
    time.sleep(2)
    r = ok("POST", C + "/api/config/publish", {"target": {"group": "GULF-FLEET"}, "notes": "e2e: PR critical, faster ack polling",
           "config": {"ackPollIntervalSec": 0.5, "priorityRules": {"PR": "CRITICAL"}}})
    ver = r["configVersion"]
    wait(lambda: status("VESSEL-01")["configVersion"] == ver, 30, "VESSEL-01 applied")
    time.sleep(6)
    deps = {d["vessel_id"]: d for d in ok("GET", C + "/api/config")["deployments"] if d["config_version"] == ver}
    v2_offline = deps["VESSEL-02"]["status"]
    link("VESSEL-02", state="CONNECTED")
    wait(lambda: status("VESSEL-02")["configVersion"] == ver, 60, "VESSEL-02 applied")
    deps2 = {d["vessel_id"]: d for d in ok("GET", C + "/api/config")["deployments"] if d["config_version"] == ver}
    pr = ok("POST", EDGE["VESSEL-02"] + "/api/records", {"recordType": "PR", "payload": {"item": "Gasket Kit", "quantity": 1}})
    ev.update(publish=r, deploymentsWhileOffline=deps, deploymentsAfter=deps2, newPrPriority=pr["priority"])
    assert v2_offline == "PENDING" and deps["VESSEL-01"]["status"] == "APPLIED"
    assert deps2["VESSEL-02"]["status"] == "APPLIED" and pr["priority"] == "CRITICAL"
    # restore the factory priority rules so later demos behave as documented
    ok("POST", C + "/api/config/publish", {"target": {"group": "GULF-FLEET"}, "notes": "e2e: restore default rules",
       "config": {"priorityRules": {"PR": "NORMAL"}}})
    return "config v%d: VESSEL-01 APPLIED immediately; VESSEL-02 PENDING while offline, APPLIED after reconnect; new PR priority=%s" % (
        ver, pr["priority"])


@test("D5-06b", "Edge-agent version update, checksum/health-check protection and rollback",
      "New version APPLIED on both vessels (checksum verified); corrupted package rejected (stays on version); failing health check "
      "rolled back automatically; operator rollback returns both vessels to the previous version")
def d5_06b(ev):
    n = int(time.time()) % 100000
    good, bad, sick = "2.%d.0" % n, "2.%d.1" % n, "2.%d.2" % n
    prev = {v: status(v)["agentVersion"] for v in EDGE}
    ok("POST", C + "/api/versions/publish", {"version": good, "target": {"vessels": list(EDGE)}, "notes": "e2e good"})
    wait(lambda: all(status(v)["agentVersion"] == good for v in EDGE), 60, "good applied")
    ok("POST", C + "/api/versions/publish", {"version": bad, "target": {"vessels": ["VESSEL-02"]}, "corrupt": True})
    ok("POST", C + "/api/versions/publish", {"version": sick, "target": {"vessels": ["VESSEL-01"]}, "failHealthCheck": True})

    def dep(v, ver):
        return [d for d in ok("GET", C + "/api/versions")["deployments"] if d["vessel_id"] == v and d["target_version"] == ver]
    db_ = wait(lambda: (lambda d: d if d and d[0]["status"] == "FAILED" else None)(dep("VESSEL-02", bad)), 60, "corrupt rejected")[0]
    ds_ = wait(lambda: (lambda d: d if d and d[0]["status"] == "FAILED" else None)(dep("VESSEL-01", sick)), 60, "health rollback")[0]
    after_fail = {v: status(v)["agentVersion"] for v in EDGE}
    ok("POST", C + "/api/versions/rollback", {"target": {"group": "GULF-FLEET"}})
    wait(lambda: all(status(v)["agentVersion"] == prev[v] for v in EDGE), 60, "rolled back")
    rb = [d for d in ok("GET", C + "/api/versions")["deployments"] if d["action"] == "ROLLBACK"][:2]
    ev.update(previous=prev, corruptDeployment=db_, healthDeployment=ds_, afterFailures=after_fail, rollbacks=rb)
    assert "checksum mismatch" in db_["detail"]["error"] and ds_["detail"]["automaticRollback"]
    assert after_fail == {v: good for v in EDGE}
    assert all(d["status"] == "ROLLED_BACK" for d in rb)
    return "%s APPLIED on both; %s rejected (checksum mismatch); %s failed health check -> auto rollback; operator rollback -> %s" % (
        good, bad, sick, prev)


# ---------------------------------------------------------------------------------------------- reliability
@test("R-01", "Edge agent crash (SIGKILL) during an outage",
      "Events created offline survive a hard kill of the agent; systemd restarts it; after reconnect all are delivered")
def r_01(ev):
    v = "VESSEL-02"
    link(v, state="DISCONNECTED")
    created = ok("POST", EDGE[v] + "/api/demo/generate", {"count": 5, "priorities": ["LOW", "CRITICAL", "NORMAL"]})
    time.sleep(2)
    pid0 = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", "d5-edge-vessel-02"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["systemctl", "kill", "-s", "KILL", "d5-edge-vessel-02"], check=True)
    time.sleep(1)
    st = wait(lambda: status(v), 40, "agent restarted")
    pid1 = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", "d5-edge-vessel-02"], capture_output=True, text=True).stdout.strip()
    pend_after_crash = st["queue"]["pending"]
    link(v, state="CONNECTED")
    wait(lambda: drained(v), 120, "drained after crash")
    acked = [edge_event(v, c["eventId"])["status"] for c in created]
    ev.update(pids=[pid0, pid1], pendingAfterRestart=pend_after_crash, statuses=acked)
    assert pid0 != pid1 and pend_after_crash >= 5 and all(s == "ACKNOWLEDGED" for s in acked)
    return "agent pid %s killed, restarted as %s with %d pending events intact; all 5 acknowledged after reconnect" % (pid0, pid1, pend_after_crash)


@test("R-02", "Central office unavailable while the vessel is online",
      "ACE still accepts (202) and MQ keeps the event; ACE rolls back, retries, then parks it on D5.ERROR (never discarded); "
      "edge does NOT mark it delivered; after central returns the edge re-sends and it is processed exactly once")
def r_02(ev):
    v = "VESSEL-01"
    err0 = mq_depths().get("D5.ERROR", 0)
    subprocess.run(["systemctl", "stop", "d5-central"], check=True)
    try:
        r = ok("POST", EDGE[v] + "/api/records", {"recordType": "GRN", "priority": "NORMAL",
               "payload": {"poNumber": "PO-77001", "item": "Fire Hose 45mm", "receivedQuantity": 4, "status": "RECEIVED"}})
        eid = r["eventId"]
        e = wait(lambda: (lambda x: x if x["status"] == "ACCEPTED" else None)(edge_event(v, eid)), 30, "ACCEPTED (202)")
        err1 = wait(lambda: (lambda d: d if d.get("D5.ERROR", 0) > err0 else None)(mq_depths()), 30, "parked on D5.ERROR")
        time.sleep(3)
        mid = edge_event(v, eid)["status"]
    finally:
        subprocess.run(["systemd-run", "--quiet", "--unit=d5-central", "--uid=aceadmin", "--gid=aceadmin", "--working-directory=" + ROOT,
                        "--setenv=PYTHONDONTWRITEBYTECODE=1", "-p", "Restart=on-failure", "/usr/bin/python3",
                        ROOT + "/central-office/app/server.py", ROOT + "/central-office/config/central.json"], check=False)
    wait(lambda: api("GET", C + "/health")[0] == 200, 30, "central back")
    fin = wait(lambda: (lambda x: x if x["status"] == "ACKNOWLEDGED" else None)(edge_event(v, eid)), 120, "acknowledged after recovery", 2)
    led = ledger(eid)
    rec = ok("GET", C + "/api/central/records/" + r["recordId"])
    ev.update(acceptedWhileDown=e, d5ErrorDepth=[err0, err1.get("D5.ERROR")], statusWhileDown=mid, final=fin, ledger=led)
    assert mid in ("ACCEPTED", "PENDING", "SENDING") and led["status"] == "PROCESSED" and len(rec["transactions"]) == 1
    return "202 from ACE while central down; D5.ERROR depth %d -> %d after MQ backout; edge kept it unacknowledged (%s); after recovery re-sent and processed once (%s)" % (
        err0, err1.get("D5.ERROR"), mid, led["transactionId"])


@test("R-03", "Intermittent satellite link (3 s up / 3 s down) with 8 events",
      "Transfers that hit a down window fail and are retried with back-off; every event is eventually acknowledged; no duplicates centrally")
def r_03(ev):
    v = "VESSEL-02"
    link(v, state="INTERMITTENT", intermittentUpSec=3, intermittentDownSec=3)
    b0 = ok("GET", NET + "/links/" + v)["stats"]["blocked"]
    created = ok("POST", EDGE[v] + "/api/demo/generate", {"count": 8, "priorities": ["CRITICAL", "NORMAL", "LOW"]})
    try:
        wait(lambda: drained(v), 240, "drained on intermittent link", 2)
    finally:
        link(v, state="CONNECTED", intermittentUpSec=15, intermittentDownSec=8)
    b1 = ok("GET", NET + "/links/" + v)["stats"]["blocked"]
    evs = [edge_event(v, c["eventId"]) for c in created]
    txs = [ledger(c["eventId"])["transactionId"] for c in created]
    ev.update(blocked=[b0, b1], attempts=[e["attempts"] for e in evs], txs=txs)
    assert all(e["status"] == "ACKNOWLEDGED" for e in evs) and len(set(txs)) == 8 and b1 > b0
    return "8/8 acknowledged; relay blocked %d requests; send attempts per event %s" % (b1 - b0, [e["attempts"] for e in evs])


@test("R-04", "Invalid event posted directly to ACE",
      "ACE validation returns HTTP 400 and parks the message on D5.ERROR; nothing reaches the D5.IN queues or the central ledger")
def r_04(ev):
    err0 = mq_depths().get("D5.ERROR", 0)
    st, body = api("POST", ACE + "/d5/v1/events", {"eventId": "not-a-uuid", "recordType": "INVOICE", "priority": "URGENT"})
    time.sleep(2)
    err1 = mq_depths().get("D5.ERROR", 0)
    ev.update(http=st, body=body, d5Error=[err0, err1])
    assert st == 400 and body["status"] == "REJECTED" and err1 == err0 + 1
    return "HTTP %d %s; D5.ERROR %d -> %d" % (st, body["errors"][:80], err0, err1)


@test("R-05", "Two vessels synchronising concurrently",
      "Each vessel delivers its own backlog in its own priority order; both complete; all events present centrally")
def r_05(ev):
    for v in EDGE:
        link(v, state="DISCONNECTED")
    made = {v: ok("POST", EDGE[v] + "/api/demo/generate", {"count": 6, "priorities": ["LOW", "NORMAL", "CRITICAL"]}) for v in EDGE}
    time.sleep(2)
    for v in EDGE:
        link(v, state="CONNECTED")
    for v in EDGE:
        wait(lambda: drained(v), 120, v + " drained", 2)
    orders = {}
    for v, cs in made.items():
        evs = sorted((edge_event(v, c["eventId"]) for c in cs), key=lambda e: e["delivery_order"])
        orders[v] = [e["priority"] for e in evs]
        assert orders[v] == sorted(orders[v], key=lambda p: RANK[p]), orders[v]
        assert all(ledger(c["eventId"])["status"] == "PROCESSED" for c in cs)
    ev.update(orders=orders)
    return "per-vessel delivery order %s (no cross-vessel ordering claimed)" % {v: "".join(p[0] for p in o) for v, o in orders.items()}


@test("E2E-ALL", "Completeness: every acknowledged vessel event exists in the central ledger",
      "For both vessels, 100% of ACKNOWLEDGED outbox events are PROCESSED centrally with ack recorded; no FAILED events except "
      "deliberate rejections")
def e2e_all(ev):
    missing, total, failed = [], 0, []
    for v in EDGE:
        for e in ok("GET", EDGE[v] + "/api/events?limit=5000"):
            if e["status"] == "ACKNOWLEDGED":
                total += 1
                l = ledger(e["eventId"])
                if l["status"] != "PROCESSED" or not l["ackRecorded"]:
                    missing.append(e["eventId"])
            elif e["status"] == "FAILED":
                failed.append(e["eventId"])
    ov = ok("GET", C + "/api/overview")
    ev.update(checked=total, missing=missing, failed=failed, overview={k: ov[k] for k in ov if k not in ("vessels", "platform")})
    assert not missing and not failed
    return "%d acknowledged events checked, 0 missing centrally; business transactions=%d; duplicates absorbed=%d" % (
        total, ov["businessTransactions"], ov["centralLedger"]["duplicateDeliveries"])


def unit_tests():
    p = subprocess.run(["runuser", "-u", "aceadmin", "--", os.path.join(ROOT, "tests", "run-unit-tests.sh")], capture_output=True, text=True)
    out = p.stdout + p.stderr
    m = re.search(r"Ran (\d+) tests", out)
    names = re.findall(r"^(test_\w+) \(([\w.\-]+)\) \.\.\. .*?(ok|FAIL|ERROR)$", out, re.M | re.S)
    return p.returncode == 0, int(m.group(1)) if m else 0, out


def main():
    for v in EDGE:
        link(v, state="CONNECTED", latencyMs=250, jitterMs=50, lossPercent=0, bandwidthKbps=0)
    for v in EDGE:
        wait(lambda: drained(v), 120, "initial drain")
    ensure_default_priority_rules()
    t0 = time.time()
    for t in (d5_01, d5_02, d5_03, d5_04, d5_05, d5_06a, d5_06b, r_01, r_02, r_03, r_04, r_05, e2e_all):
        if len(sys.argv) > 1 and t.tid not in sys.argv[1:]:
            continue
        t()
    for v in EDGE:
        link(v, state="CONNECTED", latencyMs=400, jitterMs=80)
    uok, un, uout = unit_tests()
    with open(os.path.join(ROOT, "logs", "unit-tests.log"), "w") as f:
        f.write(uout)
    passed = sum(r["passed"] for r in RESULTS)
    lines = ["# D5 Test Report", "",
             "Generated %s UTC on %s by `tests/end-to-end/run_scenarios.py` against the live stack "
             "(IBM MQ 10.0.1.0 queue manager D5QM, IBM ACE 13.0.9.0 server IS_D5_EDGE, central office, 2 edge agents, satellite simulator)."
             % (datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), os.uname().nodename), "",
             "**End-to-end / reliability: %d of %d passed** (%.0f s).  **Unit tests: %d %s** (`tests/run-unit-tests.sh`, log `logs/unit-tests.log`)."
             % (passed, len(RESULTS), time.time() - t0, un, "passed" if uok else "— FAILURES, see log"), "",
             "| ID | Test case | Expected result | Actual result | Result | Evidence |", "|---|---|---|---|---|---|"]
    for r in RESULTS:
        lines.append("| %s | %s | %s | %s | **%s** | `%s` |" % (r["id"], r["name"], r["expected"], str(r["actual"]).replace("|", "/"),
                                                            "PASS" if r["passed"] else "FAIL", r["evidence"]))
    lines += ["", "## Unit tests", "", "```", "\n".join(l for l in uout.splitlines() if re.search(r"\.\.\. (ok|FAIL|ERROR)$|^Ran |^OK|^FAILED", l)), "```", "",
              "Logs: `logs/central-office.log`, `logs/edge-vessel-01.log`, `logs/edge-vessel-02.log`, `logs/network-simulator.log`, "
              "`logs/ace-IS_D5_EDGE.console.log` (JSON lines; search by eventId)."]
    with open(os.path.join(REPORT_DIR, "test-report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n%d/%d end-to-end tests passed; unit tests: %d %s. Report: tests/reports/test-report.md" % (passed, len(RESULTS), un, "OK" if uok else "FAILED"))
    sys.exit(0 if passed == len(RESULTS) and uok else 1)


if __name__ == "__main__":
    main()
