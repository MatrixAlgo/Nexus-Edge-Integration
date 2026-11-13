"""Edge runtime: offline persistence, transactional outbox, priority dispatch, retry/back-off, ack handling,
duplicate replay, restart recovery, multi-vessel independence, configuration and version update/rollback."""
import base64, hashlib, json, os, sys, tempfile, threading, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests"))
import testpaths  # noqa: F401,E402
from common.web import TransportError  # noqa: E402
from dispatcher import Dispatcher  # noqa: E402
from management import Management  # noqa: E402
from store import EdgeStore  # noqa: E402


class FakeShore:
    """Stands in for ACE + central: link can be cut; records which eventIds arrived in which order."""
    def __init__(self):
        self.up, self.received, self.processed, self.reject_ids, self.ack_delay = True, [], {}, set(), 0

    def send(self, event_json, timeout):
        if not self.up:
            raise TransportError("ConnectionResetError: link down")
        e = json.loads(event_json)
        self.received.append(e["eventId"])
        if e["eventId"] in self.reject_ids:
            return 400, {"status": "REJECTED"}
        self.processed.setdefault(e["eventId"], e)
        return 202, {"status": "ACCEPTED", "queue": "D5.IN." + e["priority"]}

    def status(self, eid, timeout):
        if not self.up:
            raise TransportError("link down")
        if eid in self.processed:
            return 200, {"status": "PROCESSED", "ackRecorded": True, "outcome": "CREATED", "transactionId": "TX-1",
                         "receivedCount": self.received.count(eid)}
        return 200, {"status": "UNKNOWN", "ackRecorded": False}


def new_store(vessel="VESSEL-01", path=None):
    path = path or os.path.join(tempfile.mkdtemp(), "edge.db")
    return EdgeStore(path, vessel), path


def drain(store, disp, max_steps=500):
    cfg = store.config()
    for _ in range(max_steps):
        ev = store.next_event()
        if not ev:
            return
        if time.time() < disp.backoff_until:
            return
        (disp.send if ev["status"] in ("PENDING", "SENDING") else disp.check)(ev, cfg)


class OfflineAndPersistence(unittest.TestCase):
    def test_records_and_outbox_survive_restart(self):
        s, path = new_store()
        for i in range(10):
            s.create_record({"recordType": "PR", "payload": {"item": "Pump %d" % i}}, "crew")
        s.mark(s.next_event()["event_id"], status="SENDING")      # crash mid-send
        s.db.close()
        s2, _ = new_store(path=path)
        self.assertEqual(len(s2.records()), 10)
        q = s2.queue_counts()
        self.assertEqual((q["pending"], q["inFlight"]), (10, 0))   # SENDING recovered to PENDING, nothing lost

    def test_record_and_event_are_atomic(self):
        s, _ = new_store()
        st, body = s.create_record({"recordType": "BOGUS", "payload": {}}, "crew")
        self.assertEqual(st, 400)
        self.assertEqual((len(s.records()), s.queue_counts()["total"]), (0, 0))
        st, body = s.create_record({"recordType": "GRN", "payload": {"item": "x"}}, "crew")
        ev = s.event_detail(body["eventId"])
        self.assertEqual((ev["record_id"], ev["event"]["version"], ev["status"]), (body["recordId"], 1, "PENDING"))

    def test_update_carries_base_snapshot(self):
        s, _ = new_store()
        rid = s.create_record({"recordType": "MAINTENANCE", "payload": {"status": "IN_PROGRESS", "runningHours": 1}}, "c")[1]["recordId"]
        st, body = s.update_record(rid, {"changes": {"status": "COMPLETED", "runningHours": 1}}, "c")
        ev = s.event_detail(body["eventId"])["event"]
        self.assertEqual((ev["baseVersion"], ev["version"]), (1, 2))
        self.assertEqual(ev["changedFields"], {"status": "COMPLETED"})
        self.assertEqual(ev["baseSnapshot"]["status"], "IN_PROGRESS")


class PriorityDispatch(unittest.TestCase):
    def test_strict_priority_and_fifo_within_priority(self):
        s, _ = new_store()
        shore = FakeShore()
        d = Dispatcher(s, shore)
        shore.up = False
        order = ["LOW", "NORMAL", "LOW", "CRITICAL", "NORMAL", "LOW", "CRITICAL", "NORMAL", "CRITICAL", "LOW"]
        ids = [s.create_record({"recordType": "PR", "priority": p, "payload": {}}, "c")[1]["eventId"] for p in order]
        drain(s, d)                                                # link down: nothing delivered
        self.assertEqual(shore.received, [])
        self.assertEqual(s.queue_counts()["pending"], 10)
        self.assertEqual(s.get_state("link_observed"), "DOWN")
        shore.up = True
        d.link_restored()
        drain(s, d)
        rank = {"CRITICAL": 0, "NORMAL": 1, "LOW": 2}
        got = [order[ids.index(e)] for e in shore.received]
        self.assertEqual(got, sorted(order, key=lambda p: rank[p]))
        crit = [e for e in shore.received if order[ids.index(e)] == "CRITICAL"]
        self.assertEqual(crit, [ids[3], ids[6], ids[8]])           # FIFO inside a priority
        self.assertEqual(s.queue_counts()["acknowledged"], 10)
        sess = s.sync_view()["sessions"][0]
        self.assertEqual((sess["backlog_at_start"], sess["delivered"]), (10, 10))
        self.assertIsNotNone(sess["drained_at"])

    def test_same_record_events_keep_causal_order(self):
        s, _ = new_store()
        shore = FakeShore()
        d = Dispatcher(s, shore)
        shore.up = False
        rid = s.create_record({"recordType": "MAINTENANCE", "priority": "LOW", "payload": {"status": "A"}}, "c")[1]["recordId"]
        s.update_record(rid, {"changes": {"status": "B"}, "priority": "CRITICAL"}, "c")
        shore.up = True
        drain(s, d)
        evs = [shore.processed[e]["eventType"] for e in shore.received]
        self.assertEqual(evs, ["RECORD_CREATED", "RECORD_UPDATED"])   # create first although update is CRITICAL

    def test_backoff_grows_and_202_is_not_final(self):
        s, _ = new_store()
        shore = FakeShore()
        d = Dispatcher(s, shore)
        eid = s.create_record({"recordType": "PR", "payload": {}}, "c")[1]["eventId"]
        shore.up = False
        delays = []
        for _ in range(4):
            d.backoff_until = 0
            d.send(s.next_event(), s.config())
            delays.append(d.backoff_until - time.time())
        self.assertTrue(delays[0] < delays[2] < delays[3] + 1)
        self.assertEqual(s.event_detail(eid)["attempts"], 4)
        shore.up = True
        shore.processed = {}
        orig = shore.send
        shore.send = lambda ej, t: (202, {"queue": "D5.IN.NORMAL"})  # accepted by MQ but never processed
        d.send(s.next_event(), s.config())
        self.assertEqual(s.event_detail(eid)["status"], "ACCEPTED")
        d.check(s.next_event(), s.config())
        self.assertEqual(s.event_detail(eid)["status"], "ACCEPTED")   # still not acknowledged
        s.mark(eid, accepted_at="2000-01-01T00:00:00.000Z")           # pretend ackTimeout elapsed
        d.check(s.next_event(), s.config())
        self.assertEqual(s.event_detail(eid)["status"], "PENDING")    # re-sent with the same eventId
        shore.send = orig
        drain(s, d)
        self.assertEqual(s.event_detail(eid)["status"], "ACKNOWLEDGED")

    def test_rejection_is_kept_not_deleted_and_404_is_retried(self):
        s, _ = new_store()
        shore = FakeShore()
        d = Dispatcher(s, shore)
        eid = s.create_record({"recordType": "PR", "payload": {}}, "c")[1]["eventId"]
        shore.send = lambda ej, t: (404, {"error": "not found"})
        d.send(s.next_event(), s.config())
        self.assertEqual(s.event_detail(eid)["status"], "PENDING")
        shore.send = lambda ej, t: (400, {"status": "REJECTED"})
        d.backoff_until = 0
        d.send(s.next_event(), s.config())
        self.assertEqual(s.event_detail(eid)["status"], "FAILED")
        self.assertEqual(s.queue_counts()["total"], 1)

    def test_replay_resends_same_event_id(self):
        s, _ = new_store()
        shore = FakeShore()
        d = Dispatcher(s, shore)
        eid = s.create_record({"recordType": "PR", "payload": {}}, "c")[1]["eventId"]
        drain(s, d)
        self.assertEqual(s.replay(eid)[0], 200)
        drain(s, d)
        self.assertEqual(shore.received, [eid, eid])
        self.assertEqual(s.event_detail(eid)["status"], "ACKNOWLEDGED")


class MultiVessel(unittest.TestCase):
    def test_independent_stores_and_links(self):
        s1, _ = new_store("VESSEL-01")
        s2, _ = new_store("VESSEL-02")
        a, b = FakeShore(), FakeShore()
        d1, d2 = Dispatcher(s1, a), Dispatcher(s2, b)
        a.up = False
        s1.create_record({"recordType": "PR", "payload": {}}, "c")
        s2.create_record({"recordType": "PR", "payload": {}}, "c")
        drain(s1, d1)
        drain(s2, d2)
        self.assertEqual((s1.queue_counts()["pending"], s2.queue_counts()["acknowledged"]), (1, 1))
        self.assertNotEqual(s1.records()[0]["record_id"], s2.records()[0]["record_id"])


class FakeMgmt(Management):
    """Management loop with the satellite calls answered in-process."""
    def __init__(self, store, packages):
        self.store, self.vid, self.data_dir = store, store.vessel_id, tempfile.mkdtemp()
        self.dispatcher = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        self.dispatcher.start()
        self.packages, self.reports = packages, []

    def _call(self, method, path, body=None):
        if path.startswith("/packages/"):
            return 200, self.packages[path.split("/")[-1]], b""
        self.reports.append((path, body))
        return 200, {"ok": True}, b""


def package(version, health="pass", corrupt=False):
    raw = json.dumps({"manifest": {"name": "d5-edge-agent", "version": version, "healthCheck": health, "features": ["x"]}}).encode()
    sha = hashlib.sha256(raw).hexdigest()
    if corrupt:
        raw = raw.replace(b"x", b"y")
    return {"sha256": sha, "packageBase64": base64.b64encode(raw).decode()}, sha


class VersionsAndConfig(unittest.TestCase):
    def test_update_corrupt_healthfail_and_rollback(self):
        s, _ = new_store()
        p2, sha2 = package("2.0.0")
        p3, sha3 = package("3.0.0", corrupt=True)
        p4, sha4 = package("4.0.0", health="fail")
        m = FakeMgmt(s, {"2.0.0": p2, "3.0.0": p3, "4.0.0": p4})
        m.apply_version({"deploymentId": 1, "action": "UPDATE", "version": "2.0.0", "sha256": sha2})
        self.assertEqual(m.active_version(), "2.0.0")
        self.assertEqual(m.reports[-1][1]["status"], "APPLIED")
        m.apply_version({"deploymentId": 2, "action": "UPDATE", "version": "3.0.0", "sha256": sha3})
        self.assertEqual(m.active_version(), "2.0.0")
        self.assertIn("checksum mismatch", m.reports[-1][1]["detail"]["error"])
        m.apply_version({"deploymentId": 3, "action": "UPDATE", "version": "4.0.0", "sha256": sha4})
        self.assertEqual(m.active_version(), "2.0.0")
        self.assertTrue(m.reports[-1][1]["detail"]["automaticRollback"])
        m.apply_version({"deploymentId": 4, "action": "ROLLBACK"})
        self.assertEqual(m.active_version(), "1.0.0")
        self.assertEqual(m.reports[-1][1]["status"], "ROLLED_BACK")

    def test_configuration_apply_and_reject(self):
        s, _ = new_store()
        m = FakeMgmt(s, {})
        m.apply_config({"deploymentId": 1, "configVersion": 2, "config": {"priorityRules": {"PR": "CRITICAL"}}})
        self.assertEqual((s.config_version(), s.config()["priorityRules"]["PR"]), (2, "CRITICAL"))
        rid = s.create_record({"recordType": "PR", "payload": {}}, "c")[1]
        self.assertEqual(rid["priority"], "CRITICAL")              # new rule used for new records
        m.apply_config({"deploymentId": 2, "configVersion": 3, "config": {"ackTimeoutSec": -1}})
        self.assertEqual(s.config_version(), 2)
        self.assertEqual(m.reports[-1][1]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
