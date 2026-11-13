"""Priority dispatcher: one event in flight per vessel, strict CRITICAL > NORMAL > LOW order.

Delivery states:  PENDING -> SENDING -> ACCEPTED (ACE put it on a persistent MQ queue; HTTP 202)
                  -> ACKNOWLEDGED (central ledger says PROCESSED *and* the business ack from D5.OUT.ACK was recorded)
The edge never treats HTTP 202 as final. An ACCEPTED event whose status stays UNKNOWN past ackTimeoutSec is
re-sent with the same eventId (at-least-once transport; the central ledger makes processing idempotent).
"""
import json, logging, random, threading, time

from common.util import log, now_iso
from common.web import TransportError, call

L = logging.getLogger("edge.dispatcher")
# Only a validation verdict on the event itself is permanent. 404 (flow not deployed / redeploying), 408, 429, 5xx
# and network errors are infrastructure conditions: the event stays queued and is retried with back-off.
PERMANENT_REJECTIONS = (400, 422)


class HttpTransport:
    def __init__(self, link_url, events_path, status_path):
        self.link_url, self.events_path, self.status_path = link_url, events_path, status_path

    def send(self, event_json, timeout):
        return call("POST", self.link_url + self.events_path, event_json.encode(), timeout=timeout)[:2]

    def status(self, event_id, timeout):
        return call("GET", "%s%s?eventId=%s" % (self.link_url, self.status_path, event_id), timeout=timeout)[:2]


class Dispatcher(threading.Thread):
    def __init__(self, store, transport):
        super().__init__(name="dispatcher", daemon=True)
        self.store, self.t = store, transport
        self.backoff_k, self.backoff_until = 0, 0.0
        self.wake = threading.Event()
        self.running = True
        self.last_activity = None

    # called by the management loop when it observes the link coming back
    def link_restored(self):
        self.backoff_k, self.backoff_until = 0, 0.0
        self.wake.set()

    def _transport_failed(self, err):
        cfg = self.store.config()
        delay = min(cfg["retryMaxSec"], cfg["retryBaseSec"] * (2 ** self.backoff_k)) * random.uniform(0.8, 1.2)
        self.backoff_k = min(self.backoff_k + 1, 16)
        self.backoff_until = time.time() + delay
        if self.store.link_down():
            log(L, "satellite link lost - events stay in local outbox", error=err)
        return delay

    def _link_ok(self):
        self.backoff_k, self.backoff_until = 0, 0.0
        if self.store.link_up():
            log(L, "satellite link restored - resuming synchronisation")

    def run(self):
        while self.running:
            try:
                self.step()
            except Exception:  # never let the dispatcher die
                L.exception("dispatcher step failed")
                time.sleep(2)

    def step(self):
        cfg = self.store.config()
        now = time.time()
        if not cfg["syncEnabled"]:
            self.wake.wait(1.0); self.wake.clear(); return
        if now < self.backoff_until:
            self.wake.wait(min(0.5, self.backoff_until - now)); self.wake.clear(); return
        ev = self.store.next_event()
        if ev is None:
            self.wake.wait(cfg["dispatchIntervalSec"]); self.wake.clear(); return
        if ev["next_attempt_at"] > now:
            self.wake.wait(min(0.5, ev["next_attempt_at"] - now)); self.wake.clear(); return
        if ev["status"] in ("PENDING", "SENDING"):
            self.send(ev, cfg)
        elif ev["status"] == "ACCEPTED":
            self.check(ev, cfg)

    def send(self, ev, cfg):
        attempt = ev["attempts"] + 1
        self.store.mark(ev["event_id"], status="SENDING", attempts=attempt)
        started, t0 = now_iso(), time.time()
        try:
            status, body = self.t.send(ev["event_json"], cfg["requestTimeoutSec"])
        except TransportError as e:
            ms = int((time.time() - t0) * 1000)
            delay = self._transport_failed(str(e))
            self.store.mark(ev["event_id"], status="PENDING", last_error="link: " + str(e))
            self.store.attempt(ev["event_id"], attempt, "SEND", started, ms, "TRANSPORT_ERROR", None,
                               "%s; retry in %.1fs" % (e, delay))
            return
        ms = int((time.time() - t0) * 1000)
        self._link_ok()
        if status == 202:
            q = (body or {}).get("queue") if isinstance(body, dict) else None
            self.store.mark(ev["event_id"], status="ACCEPTED", accepted_at=now_iso(), mq_queue=q, last_error=None,
                            next_attempt_at=time.time() + cfg["ackPollIntervalSec"])
            self.store.attempt(ev["event_id"], attempt, "SEND", started, ms, "OK", status, body)
            self.store.set_state("last_sync_at", now_iso())
            log(L, "event accepted by ACE (persistent MQ)", eventId=ev["event_id"], priority=ev["priority"], queue=q)
        elif status in PERMANENT_REJECTIONS:
            self.store.attempt(ev["event_id"], attempt, "SEND", started, ms, "REJECTED", status, body)
            self.store.fail(ev, "rejected by integration layer: %s" % json.dumps(body))
            log(L, "event rejected - kept as FAILED", logging.WARNING, eventId=ev["event_id"], http=status)
        else:
            delay = self._transport_failed("HTTP %s" % status)
            self.store.mark(ev["event_id"], status="PENDING", last_error="HTTP %s" % status)
            self.store.attempt(ev["event_id"], attempt, "SEND", started, ms, "SERVER_ERROR", status,
                               "retry in %.1fs" % delay)

    def check(self, ev, cfg):
        started, t0 = now_iso(), time.time()
        try:
            status, body = self.t.status(ev["event_id"], cfg["requestTimeoutSec"])
        except TransportError as e:
            delay = self._transport_failed(str(e))
            self.store.attempt(ev["event_id"], ev["attempts"], "STATUS", started, int((time.time() - t0) * 1000),
                               "TRANSPORT_ERROR", None, "%s; retry in %.1fs" % (e, delay))
            return
        ms = int((time.time() - t0) * 1000)
        self._link_ok()
        st = (body or {}).get("status") if isinstance(body, dict) else None
        if status == 200 and st == "PROCESSED" and body.get("ackRecorded"):
            order = self.store.acknowledge(ev, body)
            self.store.attempt(ev["event_id"], ev["attempts"], "STATUS", started, ms, "ACKNOWLEDGED", status, body)
            self.store.set_state("last_sync_at", now_iso())
            log(L, "business acknowledgement received", eventId=ev["event_id"], recordId=ev["record_id"],
                priority=ev["priority"], outcome=body.get("outcome"), deliveryOrder=order,
                centralReceivedCount=body.get("receivedCount"))
        elif status == 200 and st == "REJECTED":
            self.store.attempt(ev["event_id"], ev["attempts"], "STATUS", started, ms, "REJECTED", status, body)
            self.store.fail(ev, "rejected by central processing: %s" % json.dumps(body.get("detail")))
        else:
            from common.util import seconds_since
            waited = seconds_since(ev["accepted_at"]) or 0
            if waited > cfg["ackTimeoutSec"]:
                self.store.attempt(ev["event_id"], ev["attempts"], "STATUS", started, ms, "ACK_TIMEOUT", status, body)
                self.store.mark(ev["event_id"], status="PENDING", next_attempt_at=0,
                                last_error="no business ack after %ds - re-sending same eventId" % waited)
                log(L, "ack timeout - re-sending (idempotent at central)", logging.WARNING, eventId=ev["event_id"])
            else:
                # PROCESSED but ack not yet recorded, or not yet processed: poll again shortly
                self.store.mark(ev["event_id"], next_attempt_at=time.time() + cfg["ackPollIntervalSec"])
