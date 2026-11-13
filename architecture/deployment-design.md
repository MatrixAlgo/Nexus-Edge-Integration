# D5 – Deployment Design

## Runtime layout (single RHEL 9.8 server)

| Unit / resource | What | Runs as | Port(s) | Limits |
|---|---|---|---|---|
| D5QM | IBM MQ queue manager (new, dedicated) | mqm | none (bindings) | – |
| `d5-ace` | ACE IntegrationServer IS_D5_EDGE | aceadmin | 7840 HTTP, 7614 admin (HTTPS) | – |
| `d5-central` | Central office, Office AMOS, fleet management, console API, dashboard | aceadmin | 8500 (127.0.0.1) | – |
| `d5-netsim` | Satellite simulator (control + per-vessel relays) | aceadmin | 8510, 8511, 8512 (127.0.0.1) | – |
| `d5-edge-vessel-01` | Edge agent VESSEL-01 | aceadmin | 8501 (127.0.0.1) | CPUQuota=100 % (1 vCPU), MemoryMax=512M, MemorySwapMax=0 |
| `d5-edge-vessel-02` | Edge agent VESSEL-02 | aceadmin | 8502 (127.0.0.1) | same |

- **Units.** The `d5-*` units are **transient** systemd units created by `systemd-run` from `deployment/scripts/start-d5.sh`. Nothing is installed in `/etc/systemd`. They disappear on stop or reboot, and they restart on failure. Permanent unit files are provided in `deployment/systemd/` for a later decision, but they are **not installed**.
- **No OS changes.** SELinux stays Enforcing, firewalld is unchanged and no ports were opened. No kernel parameters were changed and no packages were installed.
- **Dashboard access.** It is bound to 127.0.0.1:8500. Use a browser on the server's desktop (xrdp), or an SSH tunnel: `ssh -L 8500:127.0.0.1:8500 <user>@<server>`.
- **Resources.** The edge limits are real cgroup v2 limits, read back by the agent and shown on the Vessels page next to measured usage. The 2 GB disk target is reported against measured data-directory size. It is not enforced, because that would need a quota or loop device, which is an OS change.

## Files

```
D5-Edge-Demo/
├── README.md
├── architecture/          design documents (this folder)
├── ace/
│   ├── projects/D5_EdgeSync/   ACE application (msgflows + ESQL) - Toolkit importable
│   ├── flows/gen_flows.py      flow generator (source of truth for the .msgflow files)
│   ├── bar/D5_EdgeSync.bar     built BAR
│   └── deployment/             is-d5-edge.sh (server), build-deploy.sh (ibmint)
├── mq/                    queues.mqsc, scripts/setup-mq.sh, README.md
├── central-office/        app/ (server, processing, fleet), config/, database/schema.sql (+ central.db), tests/
├── edge-runtime/          agent/ (main, store, dispatcher, management, metrics), database/schema.sql, tests/
├── vessel-simulator/      vessel-01/edge.json (+ data/edge.db), vessel-02/...
├── network-simulator/     netsim.py, netsim.json (+ link-state.json)
├── dashboard/             frontend/ (index.html, app.css, app.js), backend/console.py
├── common/                shared stdlib helpers (HTTP, SQLite, logging, config contract)
├── deployment/            scripts/ start-d5.sh stop-d5.sh reset-d5.sh status-d5.sh; systemd/ (optional units)
├── tests/                 unit/, end-to-end/run_scenarios.py, run-unit-tests.sh, reports/ (test report + evidence)
├── demo/                  demo-runbook.md, sample-data.json, seed_demo.py, reset-demo.sh
├── docs/                  environment-discovery.md
├── runtime/               ACE work directory (IS_D5_EDGE)
└── logs/                  JSON-line logs per service, ACE console log, test logs
```

## Configuration outside code

| File | Contents |
|---|---|
| `central-office/config/central.json` | Ports, DB path, vessel registry, ACE/MQ endpoints |
| `central-office/config/conflict-policy.json` | Field ownership and manual-review fields |
| `vessel-simulator/vessel-0X/edge.json` | Vessel id, ports, link URL, resource targets |
| `network-simulator/netsim.json` | Relay ports and routes |
| ACE UDP `CentralBaseUrl` | Central endpoint (BAR override) |
| Runtime edge configuration | Versioned centrally, pushed through the dashboard |

No passwords or secrets exist in the POC. There is no authentication on the localhost-bound console (see README limitations).

## Lifecycle

| Action | Command (as root) | Effect |
|---|---|---|
| Start | `deployment/scripts/start-d5.sh` | Ensures D5QM is running and the queues are defined, starts IS_D5_EDGE, deploys the BAR if it is missing, starts the 4 Python units, prints status |
| Stop | `deployment/scripts/stop-d5.sh [--with-qm]` | Stops the d5-* units and IS_D5_EDGE (and D5QM if `--with-qm`) |
| Reset | `deployment/scripts/reset-d5.sh [-y]` | Deletes D5 SQLite data and link state, clears the D5.* queues, restarts and seeds baseline data |
| Status | `deployment/scripts/status-d5.sh` | Health of every D5 component |
| Rebuild ACE | `ace/deployment/build-deploy.sh` | Regenerates the flows, runs `ibmint package` and `ibmint deploy` |

## Removing D5 completely (not executed — for reference)
```bash
deployment/scripts/stop-d5.sh --with-qm
su mqm -s /bin/bash -c '/opt/mqm/bin/dltmqm D5QM'          # deletes ONLY D5QM
rm -rf /home/aceadmin/ZMI-ADNOC-POC/D5-Edge-Demo
```
