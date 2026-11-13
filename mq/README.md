# D5 – IBM MQ Resources

Everything D5 needs from IBM MQ lives on one dedicated queue manager, **`D5QM`**. No other queue manager on the server (TESTMQ, TESTMQ2, ZMIQM1, MFTQM) is read, changed, started or stopped by anything in this folder.

Design rationale, message conventions and transaction boundaries: [../architecture/mq-design.md](../architecture/mq-design.md).

## Files

| File | Purpose |
|---|---|
| `queues.mqsc` | Queue definitions for D5QM. Idempotent (`DEFINE ... REPLACE`) |
| `scripts/setup-mq.sh` | Creates D5QM if it does not exist, starts it if it is not running, applies `queues.mqsc` |

## Queue manager

| Property | Value |
|---|---|
| Name | `D5QM` |
| MQ version | 10.0.1.0 CD, `/opt/mqm`, data in `/var/mqm` |
| Created with | `crtmqm -u D5.DLQ D5QM` (as `mqm`) |
| Default queue manager | No |
| TCP listener | None. ACE (`IS_D5_EDGE`) connects with local bindings |
| Dead-letter queue | `D5.DLQ` |

## Queues

| Queue | Purpose | Persistence | Backout |
|---|---|---|---|
| `D5.IN.CRITICAL` | Inbound vessel events, CRITICAL | `DEFPSIST(YES)` | `BOTHRESH(3)` → `D5.ERROR` |
| `D5.IN.NORMAL` | Inbound vessel events, NORMAL | `DEFPSIST(YES)` | `BOTHRESH(3)` → `D5.ERROR` |
| `D5.IN.LOW` | Inbound vessel events, LOW | `DEFPSIST(YES)` | `BOTHRESH(3)` → `D5.ERROR` |
| `D5.OUT.ACK` | Business acknowledgements written after the central commit | `DEFPSIST(YES)` | `BOTHRESH(10)` → `D5.ERROR` |
| `D5.ERROR` | Validation rejects and messages backed out past the threshold | `DEFPSIST(YES)` | – |
| `D5.DLQ` | Dead-letter queue of D5QM | `DEFPSIST(YES)` | – |

All queues have `MAXDEPTH(50000)` and `MAXMSGL(4194304)`. The three inbound queues use `MSGDLVSQ(PRIORITY)`.

### Priority mapping

| Business priority | Queue | MQMD.Priority |
|---|---|---|
| CRITICAL | `D5.IN.CRITICAL` | 9 |
| NORMAL | `D5.IN.NORMAL` | 5 |
| LOW | `D5.IN.LOW` | 1 |

The strict delivery order shown in the demo is enforced by the vessel's dispatcher (one event in flight per vessel). The MQ priority and the separate queues make the backlog visible per priority; they are not what guarantees the order.

### Who uses which queue

| ACE flow (`D5_EdgeSync`) | Reads | Writes |
|---|---|---|
| `D5_EventIngest` | HTTP `POST /d5/v1/events` | `D5.IN.*` (persistent, committed before HTTP 202); `D5.ERROR` on validation failure |
| `D5_EventProcessor` | `D5.IN.*` under syncpoint | `D5.OUT.ACK`; `D5.ERROR` for central rejections; MQ backout to `D5.ERROR` after 3 failed attempts |
| `D5_AckDispatcher` | `D5.OUT.ACK` | – (records the acknowledgement centrally) |

## Setup

```bash
mq/scripts/setup-mq.sh
```

- Run as root the first time: creating the queue manager uses `su mqm`.
- Safe to re-run. If D5QM exists it is only started when needed, and the definitions are re-applied.
- Re-applying definitions does not remove messages.
- `deployment/scripts/start-d5.sh` and `reset-d5.sh` call this script; you normally do not run it by hand.

## Checking (read-only)

```bash
/opt/mqm/bin/dspmq -m D5QM
su mqm -s /bin/bash -c "echo \"DISPLAY QLOCAL('D5.*') CURDEPTH IPPROCS\" | /opt/mqm/bin/runmqsc D5QM"
su mqm -s /bin/bash -c "/opt/mqm/samp/bin/amqsbcg D5.ERROR D5QM"     # browse parked messages without removing them
```

Healthy idle state: every `D5.IN.*` queue and `D5.OUT.ACK` has `CURDEPTH(0)` and `IPPROCS(1)` (the ACE input node). `D5.ERROR` holds whatever was rejected or backed out since the last reset.

The dashboard overview shows the same queue depths live.

## Operations that change state

| Action | Command | Note |
|---|---|---|
| Stop D5QM | `deployment/scripts/stop-d5.sh --with-qm` | Stops the D5 services and ACE first, then runs `endmqm -w D5QM` |
| Start D5QM | `deployment/scripts/start-d5.sh` | Starts the queue manager if it is not running |
| Clear D5 queues | `deployment/scripts/reset-d5.sh` | Part of the full demo reset. Destroys queued messages and all D5 demo data |
| Delete D5QM | `su mqm -s /bin/bash -c '/opt/mqm/bin/dltmqm D5QM'` | Only after it is ended. Removes D5QM only. Not used by any script |

Always name `D5QM` explicitly in MQ commands. It is not the default queue manager, and `queues.mqsc` must never be applied to another queue manager.
