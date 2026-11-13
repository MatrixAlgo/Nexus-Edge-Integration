"""Central processing: idempotency, three-way merge / conflict policy, conflict resolution, office downlink."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests"))
import testpaths  # noqa: F401,E402
from common.db import Database  # noqa: E402
from common.util import new_id, now_iso  # noqa: E402
import processing  # noqa: E402

ROOT = testpaths.ROOT
POLICIES = {k: v for k, v in json.load(open(os.path.join(ROOT, "central-office", "config", "conflict-policy.json"))).items()
            if not k.startswith("_")}


def db():
    d = tempfile.mkdtemp()
    return Database(os.path.join(d, "c.db"), open(os.path.join(ROOT, "central-office", "database", "schema.sql")).read())


def event(rid="MNT-2001", etype="RECORD_CREATED", version=1, payload=None, **kw):
    e = {"eventId": new_id(), "eventType": etype, "recordId": rid, "recordType": "MAINTENANCE", "vesselId": "VESSEL-01",
         "sourceSystem": "VESSEL_AMOS", "priority": "CRITICAL", "sequenceNumber": 1, "version": version, "createdAt": now_iso(),
         "updatedAt": now_iso(), "updatedBy": "chief-engineer", "payload": payload or {"equipment": "Main Engine", "status": "IN_PROGRESS"}}
    e.update(kw)
    return e


class Idempotency(unittest.TestCase):
    def test_duplicate_event_creates_one_business_transaction(self):
        d = db()
        e = event()
        s1, b1 = processing.process_event(d, POLICIES, e, {"queue": "D5.IN.CRITICAL"})
        s2, b2 = processing.process_event(d, POLICIES, e, {"queue": "D5.IN.CRITICAL"})
        s3, b3 = processing.process_event(d, POLICIES, e, {"queue": "D5.IN.CRITICAL"})
        self.assertEqual((s1, b1["result"]), (200, "PROCESSED"))
        self.assertEqual((s2, b2["result"], s3, b3["result"]), (200, "DUPLICATE", 200, "DUPLICATE"))
        self.assertEqual(b3["transactionId"], b1["transactionId"])
        self.assertEqual(d.scalar("SELECT COUNT(*) FROM business_transactions WHERE event_id=?", (e["eventId"],)), 1)
        self.assertEqual(d.scalar("SELECT received_count FROM event_ledger WHERE event_id=?", (e["eventId"],)), 3)
        self.assertEqual(d.scalar("SELECT COUNT(*) FROM duplicate_log WHERE event_id=?", (e["eventId"],)), 2)
        self.assertEqual(d.scalar("SELECT COUNT(*) FROM records"), 1)

    def test_invalid_event_is_rejected_and_recorded(self):
        d = db()
        e = event(priority="URGENT")
        s, b = processing.process_event(d, POLICIES, e, {})
        self.assertEqual((s, b["result"]), (422, "REJECTED"))
        self.assertEqual(processing.event_status(d, e["eventId"])["status"], "REJECTED")
        self.assertEqual(d.scalar("SELECT COUNT(*) FROM business_transactions"), 0)

    def test_status_and_ack(self):
        d = db()
        e = event()
        processing.process_event(d, POLICIES, e, {})
        self.assertFalse(processing.event_status(d, e["eventId"])["ackRecorded"])
        processing.record_ack(d, {"eventId": e["eventId"]})
        st = processing.event_status(d, e["eventId"])
        self.assertTrue(st["ackRecorded"])
        self.assertEqual(st["status"], "PROCESSED")
        self.assertEqual(processing.event_status(d, new_id())["status"], "UNKNOWN")


class ThreeWayMerge(unittest.TestCase):
    base = {"equipment": "Main Engine", "status": "IN_PROGRESS", "runningHours": 12480, "technicianRemarks": "", "plannedDate": "2026-11-20"}

    def test_policy(self):
        office = dict(self.base, status="ON_HOLD", plannedDate="2026-12-02", technicianRemarks="Hold")
        vessel = {"status": "COMPLETED", "runningHours": 12500, "technicianRemarks": "Tested OK"}
        merged, dec, manual = processing.three_way_merge(self.base, office, vessel, POLICIES["MAINTENANCE"])
        by = {d["field"]: d for d in dec}
        self.assertEqual(manual, ["status"])                                   # critical field -> manual review
        self.assertEqual(merged["status"], "ON_HOLD")                         # unchanged until resolved
        self.assertEqual(by["technicianRemarks"]["decision"], "OWNER_RULE")    # vessel-owned
        self.assertEqual(merged["technicianRemarks"], "Tested OK")
        self.assertEqual(merged["runningHours"], 12500)                       # vessel-only change merged
        self.assertEqual(merged["plannedDate"], "2026-12-02")                 # office-only change kept
        self.assertEqual(by["plannedDate"]["winner"], "OFFICE")

    def test_office_owned_field_wins_and_unowned_goes_to_review(self):
        office = dict(self.base, plannedDate="2026-12-02", newField="office")
        vessel = {"plannedDate": "2026-11-21", "newField": "vessel"}
        merged, dec, manual = processing.three_way_merge(dict(self.base, newField="x"), office, vessel, POLICIES["MAINTENANCE"])
        self.assertEqual(merged["plannedDate"], "2026-12-02")
        self.assertEqual(manual, ["newField"])                                # no owner -> never last-write-wins

    def test_identical_change_is_not_a_conflict(self):
        office = dict(self.base, status="COMPLETED")
        merged, dec, manual = processing.three_way_merge(self.base, office, {"status": "COMPLETED"}, POLICIES["MAINTENANCE"])
        self.assertEqual(manual, [])
        self.assertEqual(dec[0]["decision"], "IDENTICAL")


class ConcurrentUpdateFlow(unittest.TestCase):
    def test_conflict_detect_and_resolve(self):
        d = db()
        base = {"equipment": "Main Engine", "status": "IN_PROGRESS", "runningHours": 12480}
        processing.process_event(d, POLICIES, event(payload=base), {})
        processing.office_upsert(d, {"recordId": "MNT-2001", "changes": {"status": "ON_HOLD"}}, "office")
        upd = event(etype="RECORD_UPDATED", version=2, baseVersion=1, baseSnapshot=base,
                    changedFields={"status": "COMPLETED", "runningHours": 12500},
                    payload=dict(base, status="COMPLETED", runningHours=12500))
        s, b = processing.process_event(d, POLICIES, upd, {})
        self.assertEqual(b["outcome"], "CONFLICT_MANUAL_REVIEW")
        cid = b["detail"]["conflictId"]
        rec = d.q1("SELECT * FROM records WHERE record_id='MNT-2001'")
        self.assertEqual((rec["version"], rec["conflict_open"]), (3, 1))
        s, r = processing.resolve_conflict(d, cid, {}, "x", None)
        self.assertEqual(s, 400)                                              # decision required
        s, r = processing.resolve_conflict(d, cid, {"status": "VESSEL"}, "tech-superintendent", "checked")
        self.assertEqual((s, r["finalRecord"]["status"], r["finalRecord"]["runningHours"]), (200, "COMPLETED", 12500))
        rec = d.q1("SELECT * FROM records WHERE record_id='MNT-2001'")
        self.assertEqual((rec["version"], rec["conflict_open"]), (4, 0))
        self.assertEqual(d.scalar("SELECT status FROM conflicts WHERE conflict_id=?", (cid,)), "RESOLVED")
        self.assertEqual([r["change_source"] for r in d.q("SELECT change_source FROM record_versions ORDER BY version")],
                         ["VESSEL_SYNC", "OFFICE_AMOS", "SYNC_MERGE", "CONFLICT_RESOLUTION"])
        self.assertEqual(d.scalar("SELECT COUNT(*) FROM downlink_feed WHERE vessel_id='VESSEL-01'"), 3)
        self.assertGreaterEqual(d.scalar("SELECT COUNT(*) FROM audit_log WHERE action='CONFLICT_RESOLVED'"), 1)

    def test_fast_forward_without_concurrent_change(self):
        d = db()
        base = {"status": "IN_PROGRESS"}
        processing.process_event(d, POLICIES, event(payload=base), {})
        upd = event(etype="RECORD_UPDATED", version=2, baseVersion=1, baseSnapshot=base, changedFields={"status": "COMPLETED"},
                    payload={"status": "COMPLETED"})
        s, b = processing.process_event(d, POLICIES, upd, {})
        self.assertEqual(b["outcome"], "UPDATED")
        self.assertEqual(d.scalar("SELECT COUNT(*) FROM conflicts"), 0)


if __name__ == "__main__":
    unittest.main()
