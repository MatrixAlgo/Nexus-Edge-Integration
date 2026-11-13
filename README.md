# D5 – Vessel-to-Office Edge Synchronization POC

A working, fully mocked demonstration for the ADNOC / ZMI solution. It shows how vessels and barges keep operating during satellite outages and then synchronise their operational records with the central office through IBM App Connect Enterprise and IBM MQ.

> Vessels continue their operations independently during network outages. When connectivity returns, the integration platform securely and reliably synchronises operational records with the central office, prioritising critical transactions and maintaining data consistency.

No real vessel, satellite equipment, AMOS system or ADNOC connectivity is used.

## What it demonstrates

- Offline vessel operation with a durable local store (SQLite outbox per vessel).
- A satellite link simulator that really blocks traffic (disconnect, reconnect, latency, intermittent).
- Priority-based synchronisation: CRITICAL, then NORMAL, then LOW, per vessel.
- At-least-once transport with idempotent central processing: no lost and no duplicated business transactions.
- Field-level conflict detection and resolution with ownership rules and manual review.
- Central configuration and edge-agent version management with checksum validation, health check and rollback.
- A management console for two vessel nodes.

## Architecture in one picture

```
 VESSEL-01 / VESSEL-02                    SATELLITE                     CENTRAL OFFICE
 ┌───────────────────────┐          ┌──────────────┐     ┌──────────────────────────────────────────┐
 │ Vessel AMOS (mock)    │          │ link         │     │ IBM ACE  IS_D5_EDGE  (D5_EdgeSync)       │
 │ Edge sync agent       │  HTTP    │ simulator    │     │  D5_EventIngest   POST /d5/v1/events     │
 │  SQLite outbox        ├─────────►│ relay per    ├────►│        │ validate, route, persistent put │
 │  priority dispatcher  │          │ vessel       │     │        ▼                                 │
 │  ack tracking         │◄─────────┤ (can block)  │◄────┤ IBM MQ  D5QM  D5.IN.CRITICAL/NORMAL/LOW  │
 └───────────────────────┘  status  └──────────────┘     │        ▼                                 │
                                                         │  D5_EventProcessor ──► central service   │
                                                         │        │ (syncpoint)    SQLite ledger    │
                                                         │        ▼                                 │
                                                         │  D5.OUT.ACK ─► D5_AckDispatcher          │
                                                         │  D5_EventStatus  GET /d5/v1/event-status │
                                                         │ Office AMOS (mock) · dashboard · fleet   │
                                                         └──────────────────────────────────────────┘
```

An HTTP 202 from ACE means only that MQ holds the message. The vessel marks an event ACKNOWLEDGED when the central ledger reports it processed and the business acknowledgement from `D5.OUT.ACK` has been recorded. Details: [architecture/data-flow.md](architecture/data-flow.md).

## Quick start

Run as root on the POC server. The scripts use `systemd-run` and run every service as `aceadmin`.

```bash
cd /home/aceadmin/ZMI-ADNOC-POC/D5-Edge-Demo
deployment/scripts/start-d5.sh       # D5QM -> IS_D5_EDGE (+ BAR if not deployed) -> central -> simulator -> 2 edge agents
deployment/scripts/status-d5.sh      # health of every D5 component
deployment/scripts/stop-d5.sh        # stop D5 services and IS_D5_EDGE (add --with-qm to end D5QM too)
deployment/scripts/reset-d5.sh       # DESTRUCTIVE for D5 demo data: wipe, restart and re-seed the baseline
```

**Dashboard:** `http://127.0.0.1:8500/`

It is bound to localhost. Use a browser on the server desktop, or an SSH tunnel from your machine:

```bash
ssh -L 8500:127.0.0.1:8500 <user>@<server>
```

**Credentials:** none. The console has no authentication (see Limitations).

**Demo script:** [demo/demo-runbook.md](demo/demo-runbook.md) covers scenarios D5-01 to D5-06 and the Definition-of-Done checklist.

## Components and ports

| Component | Unit / resource | Port | Source |
|---|---|---|---|
| IBM MQ queue manager | `D5QM` | none (local bindings) | [mq/](mq/README.md) |
| IBM ACE integration server | `IS_D5_EDGE` (unit `d5-ace`) | 7840 HTTP, 7614 admin (HTTPS) | `ace/` |
| Central office, Office AMOS, console API, dashboard | `d5-central` | 8500 | `central-office/`, `dashboard/` |
| Satellite simulator | `d5-netsim` | 8510 control, 8511 / 8512 relays | `network-simulator/` |
| Edge agent VESSEL-01 (Barge Marawah) | `d5-edge-vessel-01` | 8501 | `edge-runtime/`, `vessel-simulator/vessel-01/` |
| Edge agent VESSEL-02 (OSV Sir Bani Yas) | `d5-edge-vessel-02` | 8502 | `edge-runtime/`, `vessel-simulator/vessel-02/` |

All Python services bind to 127.0.0.1. Port list: [deployment/configuration/ports.md](deployment/configuration/ports.md).

## Directory layout

```
D5-Edge-Demo/
├── README.md
├── architecture/          design documents
├── ace/                   projects/D5_EdgeSync (flows + ESQL), flows/gen_flows.py, bar/, deployment/
├── mq/                    queues.mqsc, scripts/setup-mq.sh, README.md
├── central-office/        app/ (server, processing, fleet), config/, database/, tests/
├── edge-runtime/          agent/ (main, store, dispatcher, management, metrics), database/, tests/
├── vessel-simulator/      vessel-01/, vessel-02/ (edge.json + data/)
├── network-simulator/     netsim.py, netsim.json
├── dashboard/             frontend/ (HTML, CSS, JS), backend/console.py
├── common/                shared stdlib helpers
├── deployment/            scripts/ (start, stop, reset, status), systemd/ (optional units), configuration/
├── tests/                 unit/, end-to-end/run_scenarios.py, run-unit-tests.sh, reports/
├── demo/                  demo-runbook.md, sample-data.json, seed_demo.py, reset-demo.sh
├── docs/                  environment-discovery.md
├── runtime/               ACE work directory of IS_D5_EDGE
└── logs/                  JSON-line service logs, ACE console log, test logs
```

Differences from the structure in the brief:

- `ace/schemas/` is empty: event validation is done in ESQL (`ace/projects/D5_EdgeSync/D5_EdgeSync.esql`).
- `tests/integration/` and `tests/reliability/` are empty: the integration scenarios (D5-01 to D5-06) and reliability tests (R-01 to R-05) are in one runner, `tests/end-to-end/run_scenarios.py`, because they share state and run against the live stack.
- `common/`, `docs/` and `runtime/` were added.

## Tests

```bash
tests/run-unit-tests.sh                              # 22 unit tests, no services needed
python3 tests/end-to-end/run_scenarios.py            # as root, against the live stack; about 3 minutes
python3 tests/end-to-end/run_scenarios.py D5-01 R-04 # selected tests only
```

The end-to-end runner writes [tests/reports/test-report.md](tests/reports/test-report.md) and one evidence file per test in `tests/reports/evidence/`. It changes demo data and briefly kills an edge agent and stops the central service, so do not run it during a demonstration. Later tests reuse state from earlier ones (D5-03 needs D5-02, D5-04 needs D5-01), so run the whole suite unless you select independent tests.

Last recorded run, 2026-11-12 08:36 UTC: 13 of 13 end-to-end and reliability tests passed, 22 unit tests passed.

| ID | Covers |
|---|---|
| D5-01 | Normal connectivity through ACE and MQ with business acknowledgement |
| D5-02 | Disconnection: 10 records stored offline, nothing reaches MQ or the office |
| D5-03 | Reconnection: automatic drain in priority order, all acknowledged |
| D5-04 | Replay of an event ID: one business transaction only |
| D5-05 | Concurrent vessel and office update: conflict detection and resolution |
| D5-06a / b | Fleet configuration; agent version update, checksum and health-check protection, rollback |
| R-01 | Edge agent killed during an outage |
| R-02 | Central office down while the vessel is online (MQ backout to `D5.ERROR`, later re-send) |
| R-03 | Intermittent link |
| R-04 | Invalid event rejected by ACE |
| R-05 | Two vessels synchronising concurrently |
| E2E-ALL | Every acknowledged vessel event exists in the central ledger |

## Documentation

| Document | Content |
|---|---|
| [docs/environment-discovery.md](docs/environment-discovery.md) | Phase 1 findings and the approvals given |
| [architecture/solution-architecture.md](architecture/solution-architecture.md) | Components, responsibilities, design decisions, deviations from the brief |
| [architecture/data-flow.md](architecture/data-flow.md) | Event model, delivery states, acknowledgement and transaction boundaries |
| [architecture/sequence-diagrams.md](architecture/sequence-diagrams.md) | Outage and drain, duplicate replay, conflict, configuration and version update |
| [architecture/mq-design.md](architecture/mq-design.md) | Queue manager, queues, priority mapping, transactions |
| [architecture/ace-flow-design.md](architecture/ace-flow-design.md) | The four ACE flows |
| [architecture/conflict-resolution.md](architecture/conflict-resolution.md) | Detection, policy, worked example |
| [architecture/deployment-design.md](architecture/deployment-design.md) | Runtime layout, configuration files, lifecycle, removal |
| [demo/demo-runbook.md](demo/demo-runbook.md) | Step-by-step demonstration |

## Prerequisites and dependencies

- RHEL 9.8 with systemd (cgroup v2), Python 3.9 standard library with `psutil` (already installed), SQLite, `curl`.
- IBM MQ 10.0.1.0 at `/opt/mqm`; IBM ACE 13.0.9.0 at `/root/IBM/ACE/ace-13.0.9.0`. Nothing was installed or upgraded.
- OS users `aceadmin` (runs ACE and the services) and `mqm` (runs MQ commands).
- Root is needed for the lifecycle scripts and the end-to-end tests.
- No pip packages, no containers, no changes to SELinux, firewalld or kernel parameters.

## Safety and isolation

D5 uses only its own resources: queue manager `D5QM`, standalone integration server `IS_D5_EDGE`, ports 7614, 7840 and 8500–8512, the `d5-*` systemd units, and this directory. The existing queue managers (TESTMQ, TESTMQ2, ZMIQM1, MFTQM), ACE nodes (TEST, TEST2) and integration servers of the other POCs are not touched by any script. To remove D5 completely, see the last section of [architecture/deployment-design.md](architecture/deployment-design.md).

## Limitations

1. **No authentication.** The console and the service APIs have no login and are bound to localhost. The user name recorded in the audit trail is taken from the form or a request header and is not verified.
2. **Mock applications.** Vessel AMOS and Office AMOS are mock applications inside the edge agent and the central service. No real AMOS is connected.
3. **Simulated satellite link.** The link is an HTTP relay on the same server. Disconnection, latency, jitter, bandwidth and loss are simulated at that relay, not on a real network.
4. **Mock agent updates.** A version package is a manifest with feature flags. The active version and features change; the agent's Python code is not replaced.
5. **Priority order is per vessel.** Each vessel delivers CRITICAL before NORMAL before LOW. There is no ordering guarantee across vessels.
6. **At-least-once, not exactly-once.** Re-sends are expected and absorbed by the central event ledger. Exactly-once network delivery is not claimed.
7. **Management traffic bypasses ACE.** Heartbeats, configuration, versions and office-to-vessel record changes go through the satellite relay straight to the central service. Only business events and their status go through ACE and MQ.
8. **`D5.ERROR` is not reprocessed automatically.** Rejected and backed-out messages are parked there for inspection. The vessel re-sends unacknowledged events itself.
9. **Disk target is not enforced.** CPU (1 vCPU) and memory (512 MB) are real cgroup limits per edge agent. The 2 GB disk figure is a target shown next to measured usage. Production sizing is an estimate, not a measurement on vessel hardware.
10. **Not reboot-persistent.** The `d5-*` units are transient. After a reboot run `start-d5.sh`. Permanent unit files are provided in `deployment/systemd/` but are not installed.
11. **Single server, SQLite.** Central and vessel databases are SQLite files on one host. This is a POC choice, not a production design.
12. **ACE ports.** `IS_D5_EDGE` listens on all interfaces for 7840 and 7614 (ACE default). firewalld was left unchanged and does not admit these ports from outside.
13. **Licences.** The server runs IBM ACE evaluation and IBM MQ Developer editions.
14. **No automated browser tests.** The dashboard was checked by hand and by screenshots; the automated tests exercise the same APIs the dashboard uses.
