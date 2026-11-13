# D5 Test Report

Generated 2026-11-12 08:36:20 UTC on ip-172-31-35-167.ec2.internal by `tests/end-to-end/run_scenarios.py` against the live stack (IBM MQ 10.0.1.0 queue manager D5QM, IBM ACE 13.0.9.0 server IS_D5_EDGE, central office, 2 edge agents, satellite simulator).

**End-to-end / reliability: 13 of 13 passed** (188 s).  **Unit tests: 22 passed** (`tests/run-unit-tests.sh`, log `logs/unit-tests.log`).

| ID | Test case | Expected result | Actual result | Result | Evidence |
|---|---|---|---|---|---|
| D5-01 | Normal connectivity: PR onboard -> ACE REST -> MQ -> central -> business ack | Event accepted by ACE (HTTP 202, persistent put to D5.IN.NORMAL), processed centrally (1 business transaction), ack recorded via D5.OUT.ACK, edge status ACKNOWLEDGED, record visible in Office AMOS | PR-1004 4a8ed5ab ACKNOWLEDGED; ACE 202 queue=D5.IN.NORMAL mqMsgId=X'414d51204435514d20...; central TX-000013 CREATED; ack recorded at 2026-11-12T08:33:12.864Z | **PASS** | `tests/reports/evidence/D5-01.json` |
| D5-02 | Satellite disconnection: 10 records created offline | Link DISCONNECTED; all 10 records committed in vessel SQLite; 10 events pending; relay blocks the requests; office has none of them; no D5 message reaches MQ | 10 records in vessel SQLite, 10 pending in outbox (queue depth 10); edge sees link DOWN; relay blocked 5 requests; 0 of 10 at central | **PASS** | `tests/reports/evidence/D5-02.json` |
| D5-03 | Reconnection and priority order | Automatic resume without user action; the 3 CRITICAL events are delivered first, then 3 NORMAL, then 4 LOW (FIFO within priority); all 10 acknowledged and present in the central ledger | delivered PR-1005(C) > EMR-4004(C) > PR-1006(C) > EMR-4005(C) > EMR-4006(C) > GRN-3002(N) > MNT-2004(L) > MNT-2005(L) > MNT-2006(L) > MNT-2007(L); central processing order identical; session backlog 10 drained in 2026-11-12T08:33:39.301Z | **PASS** | `tests/reports/evidence/D5-03.json` |
| D5-04 | Duplicate prevention (replay of an existing eventId) | Edge replay and a raw duplicate POST to ACE are both accepted at transport level; central detects the duplicates; still exactly 1 business transaction and 1 record version for the event | eventId 4a8ed5ab received 3 times centrally; 2 duplicate deliveries logged; business transactions for the event: 1 (TX-000013) | **PASS** | `tests/reports/evidence/D5-04.json` |
| D5-05 | Conflict detection and resolution (MNT record updated on vessel and in office while offline) | Conflict detected on reconnect; status -> MANUAL_REVIEW; technicianRemarks -> OWNER_RULE (vessel); one-sided fields auto-merged; manual resolution recorded with actor; vessel and office converge on the final version; full history kept | CNF-0002 on MNT-2008: manual=status, owner rule=technicianRemarks(VESSEL), auto-merged 5 fields; resolved by technical-superintendent -> v4; vessel converged (v4) | **PASS** | `tests/reports/evidence/D5-05.json` |
| D5-06a | Fleet configuration v-next to GULF-FLEET (VESSEL-02 offline during publish) | Online vessel applies at once; offline vessel stays PENDING and applies automatically after reconnect; both report the new version and the new priority rule is effective | config v3: VESSEL-01 APPLIED immediately; VESSEL-02 PENDING while offline, APPLIED after reconnect; new PR priority=CRITICAL | **PASS** | `tests/reports/evidence/D5-06a.json` |
| D5-06b | Edge-agent version update, checksum/health-check protection and rollback | New version APPLIED on both vessels (checksum verified); corrupted package rejected (stays on version); failing health check rolled back automatically; operator rollback returns both vessels to the previous version | 2.72443.0 APPLIED on both; 2.72443.1 rejected (checksum mismatch); 2.72443.2 failed health check -> auto rollback; operator rollback -> {'VESSEL-01': '1.0.0', 'VESSEL-02': '2.0.0'} | **PASS** | `tests/reports/evidence/D5-06b.json` |
| R-01 | Edge agent crash (SIGKILL) during an outage | Events created offline survive a hard kill of the agent; systemd restarts it; after reconnect all are delivered | agent pid 72600 killed, restarted as 74093 with 5 pending events intact; all 5 acknowledged after reconnect | **PASS** | `tests/reports/evidence/R-01.json` |
| R-02 | Central office unavailable while the vessel is online | ACE still accepts (202) and MQ keeps the event; ACE rolls back, retries, then parks it on D5.ERROR (never discarded); edge does NOT mark it delivered; after central returns the edge re-sends and it is processed exactly once | 202 from ACE while central down; D5.ERROR depth 0 -> 1 after MQ backout; edge kept it unacknowledged (ACCEPTED); after recovery re-sent and processed once (TX-000032) | **PASS** | `tests/reports/evidence/R-02.json` |
| R-03 | Intermittent satellite link (3 s up / 3 s down) with 8 events | Transfers that hit a down window fail and are retried with back-off; every event is eventually acknowledged; no duplicates centrally | 8/8 acknowledged; relay blocked 23 requests; send attempts per event [2, 3, 1, 1, 1, 1, 1, 3] | **PASS** | `tests/reports/evidence/R-03.json` |
| R-04 | Invalid event posted directly to ACE | ACE validation returns HTTP 400 and parks the message on D5.ERROR; nothing reaches the D5.IN queues or the central ledger | HTTP 400 eventId must be a lowercase UUID; recordId is required; vesselId is required; re; D5.ERROR 1 -> 2 | **PASS** | `tests/reports/evidence/R-04.json` |
| R-05 | Two vessels synchronising concurrently | Each vessel delivers its own backlog in its own priority order; both complete; all events present centrally | per-vessel delivery order {'VESSEL-01': 'CCNNLL', 'VESSEL-02': 'CCNNLL'} (no cross-vessel ordering claimed) | **PASS** | `tests/reports/evidence/R-05.json` |
| E2E-ALL | Completeness: every acknowledged vessel event exists in the central ledger | For both vessels, 100% of ACKNOWLEDGED outbox events are PROCESSED centrally with ack recorded; no FAILED events except deliberate rejections | 52 acknowledged events checked, 0 missing centrally; business transactions=52; duplicates absorbed=6 | **PASS** | `tests/reports/evidence/E2E-ALL.json` |

## Unit tests

```
test_fast_forward_without_concurrent_change (central-office.tests.test_processing.ConcurrentUpdateFlow) ... ok
test_duplicate_event_creates_one_business_transaction (central-office.tests.test_processing.Idempotency) ... ok
test_invalid_event_is_rejected_and_recorded (central-office.tests.test_processing.Idempotency) ... ok
test_status_and_ack (central-office.tests.test_processing.Idempotency) ... ok
test_identical_change_is_not_a_conflict (central-office.tests.test_processing.ThreeWayMerge) ... ok
test_office_owned_field_wins_and_unowned_goes_to_review (central-office.tests.test_processing.ThreeWayMerge) ... ok
test_policy (central-office.tests.test_processing.ThreeWayMerge) ... ok
test_independent_stores_and_links (edge-runtime.tests.test_edge.MultiVessel) ... ok
test_record_and_event_are_atomic (edge-runtime.tests.test_edge.OfflineAndPersistence) ... ok
test_records_and_outbox_survive_restart (edge-runtime.tests.test_edge.OfflineAndPersistence) ... ok
test_update_carries_base_snapshot (edge-runtime.tests.test_edge.OfflineAndPersistence) ... ok
test_replay_resends_same_event_id (edge-runtime.tests.test_edge.PriorityDispatch) ... ok
test_same_record_events_keep_causal_order (edge-runtime.tests.test_edge.PriorityDispatch) ... ok
test_strict_priority_and_fifo_within_priority (edge-runtime.tests.test_edge.PriorityDispatch) ... ok
test_configuration_apply_and_reject (edge-runtime.tests.test_edge.VersionsAndConfig) ... ok
test_default_is_valid (tests.unit.test_edgeconfig.ConfigValidation) ... ok
test_partial_config_merges_with_defaults (tests.unit.test_edgeconfig.ConfigValidation) ... ok
test_rejects_bad_values (tests.unit.test_edgeconfig.ConfigValidation) ... ok
Ran 22 tests in 1.337s
OK
```

Logs: `logs/central-office.log`, `logs/edge-vessel-01.log`, `logs/edge-vessel-02.log`, `logs/network-simulator.log`, `logs/ace-IS_D5_EDGE.console.log` (JSON lines; search by eventId).
