# D5 Recovery Report (read-only review)

Prepared 2026-11-12 (UTC) from a read-only inspection of `/home/aceadmin/ZMI-ADNOC-POC/D5-Edge-Demo`, the live services, IBM MQ / ACE state, the test evidence and the original session's transcript.

The implementation is functionally complete and tested. What remains is the closing documentation and a final clean-state verification. Nothing was modified, restarted or created in the project during the review (this report file is the only addition, written afterwards on request).

The original session's transcript is still on this server (`/root/.claude/projects/-root-ZMID5POC/32878794-f51c-4b5f-b907-52a4d25eae90.jsonl`), so the stop point below is read directly from it.

## Where the previous session stopped

- **Last completed action (08:35:24 UTC):** it wrote the optional systemd unit files, `deployment/configuration/ports.md` and `demo/reset-demo.sh`.
- **Limit hit (08:35:25):** this happened in the middle of the documentation step, while the test suite was still running in the background.
- **Test suite finished (08:36:20):** exit code 0, but the session never got to read or report the result.
- **Later sessions:** one read-only audit was started at 10:47 and interrupted at 10:50; it changed nothing.

## Current runtime state (live)

| Component | State |
|---|---|
| Queue manager `D5QM` | Running; the `D5.IN.*` and `D5.OUT.ACK` queues are empty and each has an ACE input handle open |
| `IS_D5_EDGE` (unit `d5-ace`, admin 7614, HTTP 7840) | Running; application `D5_EdgeSync` with all 4 flows started |
| `d5-central`, `d5-netsim`, `d5-edge-vessel-01`, `d5-edge-vessel-02` | Active; both vessels online and heartbeating |
| Dashboard | `http://127.0.0.1:8500/` returns 200 (localhost only, no authentication) |
| Existing resources (TESTMQ2, ZMIQM1, node TEST, the other integration servers) | Running and untouched; TESTMQ and MFTQM were already "Ended unexpectedly" before D5 started |

## Completed and tested

Test evidence is in `tests/reports/test-report.md`, `tests/reports/evidence/*.json`, `logs/e2e-run.log` and `logs/unit-tests.log`. The run was 13 of 13 end-to-end and reliability tests passed, plus 22 unit tests.

| Requirement | Implementation | Test evidence |
|---|---|---|
| Phase 1: discovery | `docs/environment-discovery.md` | Four approvals recorded (D5QM, standalone integration server, workspace, stdlib-only Python) |
| Phase 3 / Module 5: MQ | `mq/queues.mqsc`, `mq/scripts/setup-mq.sh` | Live queues match the definitions |
| Module 5: ACE | `ace/projects/D5_EdgeSync/` (4 flows and ESQL), `ace/flows/gen_flows.py`, `ace/bar/D5_EdgeSync.bar`, `ace/deployment/*.sh` | D5-01, R-02, R-04 |
| Modules 1, 6, 7: central office | `central-office/app/{server,processing,fleet}.py`, `central-office/database/schema.sql`, `central-office/config/conflict-policy.json` | D5-04, D5-05, unit tests |
| Modules 2, 4: edge runtime | `edge-runtime/agent/{main,store,dispatcher,management,metrics}.py`, `edge-runtime/database/schema.sql` | D5-02, D5-03, R-01, R-05 |
| Module 3: satellite simulator | `network-simulator/netsim.py` (a real relay that blocks requests) | D5-02 (5 requests blocked), R-03 |
| Module 8: dashboard | `dashboard/frontend/{index.html,app.js,app.css}`, `dashboard/backend/console.py` | Screenshots taken in the original session; no automated UI test |
| Scenario D5-06 and section 7 footprint | Config and version publish, checksum check, automatic and operator rollback; cgroup limits of 1 vCPU / 512 MB with measured usage reported separately | D5-06a, D5-06b |
| Lifecycle scripts | `deployment/scripts/{start,stop,reset,status}-d5.sh` | Start and reset were executed during the build |
| Architecture documents | All 7 files in `architecture/` | Not applicable |

The dashboard has all 7 required pages plus Vessel AMOS, Office AMOS and Audit pages, and reads everything from the live APIs.

### Test results (from `tests/reports/test-report.md`, generated 2026-11-12 08:36:20 UTC)

| ID | Test case | Result | Evidence |
|---|---|---|---|
| D5-01 | Normal connectivity: PR onboard -> ACE REST -> MQ -> central -> business ack | PASS | `tests/reports/evidence/D5-01.json` |
| D5-02 | Satellite disconnection: 10 records created offline | PASS | `tests/reports/evidence/D5-02.json` |
| D5-03 | Reconnection and priority order | PASS | `tests/reports/evidence/D5-03.json` |
| D5-04 | Duplicate prevention (replay of an existing eventId) | PASS | `tests/reports/evidence/D5-04.json` |
| D5-05 | Conflict detection and resolution | PASS | `tests/reports/evidence/D5-05.json` |
| D5-06a | Fleet configuration to GULF-FLEET (VESSEL-02 offline during publish) | PASS | `tests/reports/evidence/D5-06a.json` |
| D5-06b | Edge-agent version update, checksum / health-check protection and rollback | PASS | `tests/reports/evidence/D5-06b.json` |
| R-01 | Edge agent crash (SIGKILL) during an outage | PASS | `tests/reports/evidence/R-01.json` |
| R-02 | Central office unavailable while the vessel is online | PASS | `tests/reports/evidence/R-02.json` |
| R-03 | Intermittent satellite link with 8 events | PASS | `tests/reports/evidence/R-03.json` |
| R-04 | Invalid event posted directly to ACE | PASS | `tests/reports/evidence/R-04.json` |
| R-05 | Two vessels synchronising concurrently | PASS | `tests/reports/evidence/R-05.json` |
| E2E-ALL | Completeness: 52 acknowledged events, 0 missing centrally, 6 duplicates absorbed | PASS | `tests/reports/evidence/E2E-ALL.json` |

## Partial or pending

1. **`README.md` is missing.** `architecture/deployment-design.md` refers to it, including a "README limitations" section.
2. **`demo/demo-runbook.md` is missing.** This is the Phase 8 deliverable.
3. **`mq/README.md` is missing.**
4. **The final summary was never delivered.** Definition of Done asks for the dashboard URL, startup instructions, test results and outstanding limitations.
5. **The system is in a post-test state, not a demo baseline:**
   - VESSEL-01 is on agent 1.0.0 and VESSEL-02 is on 2.0.0.
   - Configuration is at v4, and test packages `2.72443.x` are in the catalogue.
   - One conflict is still open.
   - `D5.ERROR` holds 2 messages left by tests R-02 and R-04.
   - A `reset-d5.sh` run is needed before a demo; it is destructive to D5 data only and needs approval first.
6. **The D5-03 report text does not match its result.**
   - The expected text says "3 CRITICAL, then 3 NORMAL, then 4 LOW"; the run delivered 5 CRITICAL, 1 NORMAL, 4 LOW.
   - The order was correct and the test passed.
   - The cause is that configuration v2 from earlier manual testing still mapped PR to CRITICAL.
   - A re-run from a clean reset should fix this, or the expected text in `tests/end-to-end/run_scenarios.py` should be corrected.
7. **`stop-d5.sh` was never executed on its own.** Reset exercises the same stop path, but the standalone script and its `--with-qm` option are untested.
8. **Three directories from the brief are empty:**
   - `tests/integration/` and `tests/reliability/`: those tests live in `tests/end-to-end/run_scenarios.py`.
   - `ace/schemas/`: validation is done in ESQL.
   - Each needs a short note or should be removed.
9. **Services are transient systemd units.** They do not survive a reboot. This is by design; installing the permanent units in `deployment/systemd/` is a privileged change that needs approval.
10. **A stray screenshot helper is still running.** It is PID 70914, listening on 127.0.0.1:8599, left by the original session. It is harmless, but should be stopped with approval.

## Exact next unfinished task

Continue the documentation step where it broke off: write `demo/demo-runbook.md`, then `README.md` and `mq/README.md`. These are new files only and touch no running component.

After that, with approval:

1. Run `reset-d5.sh -y` to return to a clean baseline.
2. Re-run the unit and end-to-end suites from that baseline, which also resolves item 6.
3. Deliver the final Definition of Done summary.
