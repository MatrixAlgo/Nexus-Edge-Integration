#!/usr/bin/env python3
"""Load demo/sample-data.json onto the vessels and wait until every record is acknowledged by the office."""
import json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORTS = {"VESSEL-01": 8501, "VESSEL-02": 8502}


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-D5-User": "demo-seed"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


data = json.load(open(os.path.join(HERE, "sample-data.json")))
for vessel, recs in data.items():
    if vessel.startswith("_"):
        continue
    for r in recs:
        try:
            out = call("POST", "http://127.0.0.1:%d/api/records" % PORTS[vessel], r)
            print("  %s %s created (event seq %s)" % (vessel, out["recordId"], out["sequenceNumber"]))
        except urllib.error.HTTPError as e:
            print("  %s %s: %s" % (vessel, r["recordId"], e.read().decode()[:120]))
deadline = time.time() + 120
while time.time() < deadline:
    open_ = sum(call("GET", "http://127.0.0.1:%d/api/status" % p)["queue"]["pending"] +
                call("GET", "http://127.0.0.1:%d/api/status" % p)["queue"]["awaitingAck"] for p in PORTS.values())
    if open_ == 0:
        print("seed complete - all baseline records acknowledged by the office")
        sys.exit(0)
    time.sleep(1)
print("WARNING: seed records still open after 120s (is the satellite link connected?)")
sys.exit(1)
