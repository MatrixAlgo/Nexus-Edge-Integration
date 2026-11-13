# D5 – Demonstration Runbook

Vessel-to-Office Edge Synchronization POC. This runbook walks through the six scenarios (D5-01 to D5-06) on the dashboard and maps them to the 15 Definition-of-Done checks.

Everything shown is live: the dashboard reads the vessel SQLite stores, the central database, IBM MQ queue depths and IBM ACE flow state. Nothing is hardcoded.

**The statement to land:** vessels keep working during satellite outages; when the link returns, the platform synchronises their records reliably, critical transactions first, without losing or duplicating business transactions.

## 1. Before the demo

### 1.1 Access

The dashboard is bound to localhost on the server. There is no login.

| How | URL |
|---|---|
| Browser on the server desktop (xrdp) | `http://127.0.0.1:8500/` |
| From your laptop | `ssh -L 8500:127.0.0.1:8500 <user>@<server>`, then `http://127.0.0.1:8500/` |

### 1.2 Start and check (as root)

```bash
cd /home/aceadmin/ZMI-ADNOC-POC/D5-Edge-Demo
deployment/scripts/start-d5.sh      # safe to run when already started
deployment/scripts/status-d5.sh
```

Expected status: `D5QM STATUS(Running)`, `IS_D5_EDGE running`, and the four `d5-*` units `active healthy`.

### 1.3 Clean baseline

The scenarios below assume the baseline created by the reset script:

- Both vessels online, agent version 1.0.0, configuration version 1.
- Baseline records synchronised: `MNT-2001`, `PR-1000`, `GRN-3000` (VESSEL-01) and `MNT-2501`, `PR-1500` (VESSEL-02).
- No conflicts, `D5.*` queues empty.

```bash
deployment/scripts/reset-d5.sh      # asks for confirmation; -y skips the question
```

> **Reset is destructive for D5 demo data.** It deletes the D5 SQLite databases, the link state and the D5 service logs, clears the `D5.*` queues on D5QM, restarts the D5 services and re-seeds the baseline. It does not touch any other queue manager, ACE server or project.

If you demo without a reset, the flow still works, but the numbers differ: configuration and agent versions will be higher than stated below, and earlier test records and conflicts will be visible.

### 1.4 Do not run the automated tests during a demo

`tests/end-to-end/run_scenarios.py` creates records, changes the link state, publishes configuration, kills an edge agent and stops the central service for a few seconds.

## 2. Dashboard pages

| Menu entry | Use in the demo |
|---|---|
| Executive overview | Fleet KPIs, vessel cards with a Disconnect / Reconnect button, live MQ queue depths and ACE flow state |
| Vessels & satellite | Vessel table (connectivity, agent version, CPU / memory / disk, queue depth, last sync) and the satellite link simulator |
| Event monitoring | Every vessel event with priority, delivery status, retries and acknowledgement; **Replay** button |
| Synchronization | Offline duration, pending messages, delivery sequence, central processing order |
| Vessel AMOS (onboard) | Create and edit records on a vessel; bulk test data generator |
| Office AMOS | Central records; open one to edit it as the office |
| Conflicts | Conflict list and the four-column review and resolution screen |
| Edge configuration | Publish a configuration version to a vessel or group; deployment status |
| Edge versions | Publish a mock agent version, checksum and health-check protection, rollback |
| Audit & history | Audit log and end-to-end history of one record |

## 3. Scenarios

### D5-01 – Normal connectivity

1. **Executive overview:** show 2 vessels online and the IBM MQ and IBM ACE cards (D5QM running, four flows running).
2. **Vessel AMOS (onboard)**, tab VESSEL-01: create a record with type `PR` (for example item *Hydraulic Pump*, quantity 2). Leave the priority blank so the rule applies (NORMAL). Press **Save onboard**.
3. **Event monitoring:** the event moves PENDING → ACCEPTED → ACKNOWLEDGED within a few seconds. Click the event ID to show its delivery attempts.
4. **Office AMOS:** the PR is present centrally.

What to say:

- The vessel posted the event to the ACE REST API (`POST /d5/v1/events` on port 7840). ACE validated it and put it as a persistent message on `D5.IN.NORMAL`.
- ACE answered HTTP 202. That only means MQ has the message. The vessel does not treat it as done.
- A second ACE flow read the queue under syncpoint, called central processing, and put the business acknowledgement on `D5.OUT.ACK`.
- The vessel marked the event ACKNOWLEDGED only after the central ledger reported it processed and acknowledged.

### D5-02 – Satellite disconnection

1. **Vessels & satellite:** on the VESSEL-01 link card choose **DISCONNECTED** and press **Apply link settings** (or use the Disconnect button on the overview card).
2. The vessel shows OFFLINE. VESSEL-02 stays online, which shows the vessels are independent.
3. **Vessel AMOS (onboard)**, tab VESSEL-01, *Bulk test data*: 10 records, mix **Mixed priorities (C/N/L)**, press **Generate records**.
4. The onboard records list shows all 10. They are committed in the vessel's SQLite store (`vessel-simulator/vessel-01/data/edge.db`).
5. **Vessels & satellite** or **Synchronization:** queue depth for VESSEL-01 is 10 pending, and the offline duration is counting.
6. **Office AMOS:** none of the 10 records is there. On the overview, the `D5.IN.*` queue depths are still 0.

What to say:

- The disconnect is real. The vessel reaches the shore only through a relay; when the link is down the relay refuses the request. The "blocked" counter on the link card rises with every attempt.
- Onboard work is unaffected: the record and its outbound event are written in one local transaction.

Optional proof on the server (read-only; the `sqlite3` command-line tool is not installed, so this uses Python):

```bash
python3 -c "
import sqlite3
con = sqlite3.connect('file:vessel-simulator/vessel-01/data/edge.db?mode=ro', uri=True)
for row in con.execute('SELECT status, priority, COUNT(*) FROM outbound_events GROUP BY 1, 2'): print(row)"
```

### D5-03 – Reconnection and priority

1. Open **Synchronization** so the delivery sequence is visible.
2. In a second tab or on the overview, **reconnect** VESSEL-01.
3. Without any further action the backlog drains. The delivery sequence shows all CRITICAL events first, then NORMAL, then LOW, first-in first-out inside each priority.
4. *Central processing order* on the same page shows the office processed them in the same order.
5. **Event monitoring:** all 10 events are ACKNOWLEDGED.

What to say:

- The vessel keeps one event in flight at a time and always picks the highest priority next. That makes the order deterministic.
- The ordering guarantee is per vessel. Separate vessels drain at the same time and independently; no global order across vessels is claimed.
- IBM MQ also carries the priority (separate queues, MQMD priority 9 / 5 / 1), documented in `architecture/mq-design.md`.

Note: which record types are CRITICAL, NORMAL or LOW comes from the active priority rules. With the default rules the mixed generator gives 3 CRITICAL, 3 NORMAL and 4 LOW per 10 records.

### D5-04 – Duplicate prevention

1. **Event monitoring:** pick an ACKNOWLEDGED event (the PR from D5-01) and press **Replay**. The vessel re-sends the same event ID.
2. The event goes back through ACE and MQ and is acknowledged again.
3. **Executive overview:** *Duplicates suppressed* increases by one; *business transactions* does not change.
4. **Audit & history:** enter the record ID and press **Show history**. There is exactly one business transaction for that event, and the duplicate delivery is logged.

What to say:

- Transport is at-least-once. Re-sends happen in real life (ack timeout, crash recovery).
- The central ledger is keyed by event ID, so a repeated event never creates a second business transaction. Exactly-once network delivery is not claimed.

### D5-05 – Conflict resolution

Uses the seeded record `MNT-2001` (Main Engine, status IN_PROGRESS, version 1).

1. **Disconnect** VESSEL-01.
2. **Vessel AMOS (onboard)**, VESSEL-01: press **Edit** on `MNT-2001`. Set `status` to `COMPLETED`, add text to `technicianRemarks` and `workPerformed`. Press **Save change onboard**.
3. **Office AMOS:** press **Open** on `MNT-2001`. Set `status` to `ON_HOLD`, add text to `technicianRemarks`, and change `plannedDate`. Press **Save office update**.
4. **Reconnect** VESSEL-01.
5. **Conflicts:** a new conflict appears with status OPEN (the menu shows a badge). Press **Review**.
6. The screen shows four columns: original record, vessel version, office version, and the merged record so far. Below it, the decision per field:
   - `workPerformed` and `plannedDate` were changed on one side only: merged automatically.
   - `technicianRemarks` was changed on both sides and is vessel-owned: the vessel value wins by rule.
   - `status` was changed on both sides and is a critical field: held for manual review.
7. Under *Manual resolution* choose the vessel value, the office value or a custom value for `status`, keep or change *Resolved by*, add a note, and press **Apply resolution**.
8. The conflict becomes RESOLVED with the final record version. **Vessel AMOS (onboard):** the vessel receives the final version over the link.
9. **Audit & history:** show the record history with every version, the conflict and the person who resolved it.

What to say:

- This is not last-write-wins. The rules are per field and live in `central-office/config/conflict-policy.json`.
- Until the conflict is resolved, the office record keeps the safely merged fields and the disputed field is not overwritten.

### D5-06 – Fleet configuration and version update

Configuration:

1. **Edge configuration:** the form title shows the next version (version 2 from a clean baseline).
2. Select group `GULF-FLEET` (or tick both vessels). Change a synchronisation setting, for example the acknowledgement poll interval, or a priority rule such as PR → CRITICAL. Add a release note and press **Publish configuration**.
3. *Deployment status* shows APPLIED for both vessels within a few seconds; *Active configuration per vessel* shows the new version on both.
4. Optional: disconnect VESSEL-02 before publishing. Its deployment stays PENDING and applies by itself after reconnect.

If you changed a priority rule, publish another version that restores it before repeating D5-03.

Agent version:

1. **Edge versions:** keep version `2.0.0`, select `GULF-FLEET`, press **Publish & deploy**.
2. *Deployment status* shows each vessel download the package, verify the SHA-256 checksum, stage it, activate it and pass the health check. *Fleet versions* shows 2.0.0 with the previous version 1.0.0.
3. Protection, optional: publish `2.0.1` with **Simulate corrupted package** ticked. The vessels reject it (checksum mismatch) and stay on 2.0.0.
4. Protection, optional: publish `2.0.2` with **Simulate failing post-update health check** ticked. The vessels activate it, the health check fails, and they roll back automatically.
5. **Roll back to previous version:** select the target and press **Roll back**. The vessels return to the previous version.

What to say:

- Configuration and versions travel over the same satellite link, so an offline vessel simply catches up later.
- The console never sends shell commands. The vessel pulls a package, validates it and applies it through a fixed, controlled procedure.
- The package is a mock (a manifest with feature flags). The active version and features change; the Python code is not replaced.

## 4. Optional extras

| Topic | How to show it |
|---|---|
| Latency | **Vessels & satellite:** set *Latency (ms)* to 2000 and apply; deliveries visibly slow down |
| Intermittent link | Choose **INTERMITTENT** with up and down seconds; events that hit a down window are retried with back-off and all arrive once |
| Lightweight footprint | **Vessels & satellite** table: measured CPU and memory next to the configured limit (1 vCPU, 512 MB, enforced by cgroup) and the 2 GB disk target. Measured, limit and estimate are labelled separately |
| MQ view | `su mqm -s /bin/bash -c "echo \"DISPLAY QLOCAL('D5.*') CURDEPTH\" \| /opt/mqm/bin/runmqsc D5QM"` |
| Trace one event | `grep <eventId> logs/*.log` shows the event in the edge, ACE-facing and central logs |

## 5. Definition-of-Done checklist

| # | Check | Where |
|---|---|---|
| 1 | Start the complete D5 environment | `deployment/scripts/start-d5.sh` |
| 2 | Open the dashboard | `http://127.0.0.1:8500/` |
| 3 | See Vessel 01 and Vessel 02 | Executive overview, Vessels & satellite |
| 4 | Create a purchase requisition onboard | D5-01 |
| 5 | Disconnect the satellite | D5-02 step 1 |
| 6 | Create additional records offline | D5-02 step 3 |
| 7 | See records queued locally | D5-02 steps 4–5 |
| 8 | Reconnect the satellite | D5-03 step 2 |
| 9 | Critical messages delivered first | D5-03 step 3 |
| 10 | All transactions arrive centrally | D5-03 step 5, Office AMOS |
| 11 | Replay an event, duplicate prevention | D5-04 |
| 12 | Concurrent update and conflict resolution | D5-05 |
| 13 | Configuration update to both vessels | D5-06 configuration |
| 14 | Controlled version update | D5-06 agent version |
| 15 | Complete audit and synchronisation history | Audit & history, Synchronization |

## 6. After the demo

```bash
deployment/scripts/stop-d5.sh            # stops the D5 services and IS_D5_EDGE; D5QM keeps running
deployment/scripts/stop-d5.sh --with-qm  # also ends queue manager D5QM
```

Data is kept across stop and start. Use the reset script to return to the baseline.

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| Dashboard does not load | `deployment/scripts/status-d5.sh`; `journalctl -u d5-central -n 50` |
| Events stay PENDING | Link state on **Vessels & satellite**; `logs/edge-vessel-0X.log` |
| Events stay ACCEPTED (no acknowledgement) | ACE flows on the overview; `logs/ace-IS_D5_EDGE.console.log`; depth of `D5.ERROR` |
| Messages on `D5.ERROR` | Rejected or backed-out messages are parked there, never discarded. Browse with `su mqm -s /bin/bash -c "/opt/mqm/samp/bin/amqsbcg D5.ERROR D5QM"` |
| A vessel shows a stale heartbeat | `systemctl status d5-edge-vessel-0X`; the unit restarts itself on failure |
