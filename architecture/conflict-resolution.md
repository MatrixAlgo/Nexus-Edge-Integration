# D5 – Conflict Resolution

## Detection
Every `RECORD_UPDATED` event carries the vessel's **base version**, a **base snapshot** (the record before the change) and the **changed fields**.

When the event arrives, central checks two things:
- If the central record is still at `baseVersion` **and** its content equals the snapshot, the change is a fast-forward (outcome `UPDATED`).
- Otherwise the office changed the record concurrently, and the event goes to a **three-way merge**: base vs office-current vs vessel change.

## Policy (`central-office/config/conflict-policy.json`)

| Situation | Decision |
|---|---|
| Field changed on the vessel only | AUTO_MERGE (vessel value) |
| Field changed in the office only | AUTO_MERGE (office value kept) |
| Both sides changed it to the same value | IDENTICAL (no conflict) |
| Both sides changed it differently, and the field is in `manualReview` | **MANUAL_REVIEW**. The office value is held until an administrator decides |
| Both sides changed it differently, and the owner is VESSEL | OWNER_RULE (vessel wins) |
| Both sides changed it differently, and the owner is OFFICE | OWNER_RULE (office wins) |
| Both sides changed it differently, and no owner is defined | MANUAL_REVIEW. The engine never falls back to last-write-wins |

Field ownership for MAINTENANCE:

| Owner | Fields |
|---|---|
| VESSEL | `runningHours`, `workPerformed`, `technicianRemarks`, `completedBy` |
| OFFICE | `plannedDate`, `assignedVendor`, `budgetCode`, `officeRemarks` |
| Manual review | `status` |

PR, GRN and EMERGENCY_MAINTENANCE have their own rules in the same file.

## Result
One central transaction does all of the following:
- Writes the merged record as a new version (`change_source = SYNC_MERGE`).
- Stores a `conflicts` row with the base, office, vessel and merged payloads and every field decision, with status `AUTO_RESOLVED` or `OPEN`.
- Writes an audit entry.
- Sends the merged record to the vessel through the downlink. The vessel shows `CONFLICT_REVIEW` while the conflict is open.

An administrator resolves an OPEN conflict on **Conflicts**. For each manual field they choose VESSEL, OFFICE or a custom value, and add their name and a note. Central then:
- writes a new version (`CONFLICT_RESOLUTION`),
- marks the conflict RESOLVED with the actor and timestamp,
- writes an audit entry,
- sends the final record to the vessel.

The full history stays in `record_versions`, `conflicts` and `audit_log`.

## Worked example (scenario D5-05, executed by the e2e tests)

| Field | Base v1 | Vessel v2 (offline) | Office v2 | Decision | Result |
|---|---|---|---|---|---|
| status | IN_PROGRESS | COMPLETED | ON_HOLD | MANUAL_REVIEW → admin chose VESSEL | COMPLETED |
| technicianRemarks | "" | Tested OK at 85% load | Hold - spares | OWNER_RULE (vessel) | Tested OK at 85% load |
| runningHours | 12480 | 12500 | 12480 | AUTO_MERGE (vessel) | 12500 |
| workPerformed | "" | Head overhauled… | "" | AUTO_MERGE (vessel) | Head overhauled… |
| plannedDate | 2026-11-20 | 2026-11-20 | 2026-12-02 | AUTO_MERGE (office) | 2026-12-02 |
| assignedVendor / officeRemarks | "" | "" | set | AUTO_MERGE (office) | office values |

The versions are then: v1 VESSEL_SYNC → v2 OFFICE_AMOS → v3 SYNC_MERGE → v4 CONFLICT_RESOLUTION. The vessel converges to v4.
