# D5 – IBM MQ Design

**Queue manager:** `D5QM`. It is new and dedicated to D5, and runs on IBM MQ 10.0.1.0 CD (Developer licence), Installation1. It was created with `crtmqm -u D5.DLQ D5QM`. It is not the default QM and has no TCP listener: ACE connects with local bindings. TESTMQ, TESTMQ2, ZMIQM1 and MFTQM were not modified.

Definitions: [`mq/queues.mqsc`](../mq/queues.mqsc) (idempotent, `REPLACE`). Apply them with `mq/scripts/setup-mq.sh`.

| Queue | Purpose | DEFPSIST | Backout | Other |
|---|---|---|---|---|
| D5.IN.CRITICAL | Inbound vessel events, CRITICAL | YES | BOTHRESH 3 → D5.ERROR | MSGDLVSQ(PRIORITY) |
| D5.IN.NORMAL | Inbound vessel events, NORMAL | YES | BOTHRESH 3 → D5.ERROR | MSGDLVSQ(PRIORITY) |
| D5.IN.LOW | Inbound vessel events, LOW | YES | BOTHRESH 3 → D5.ERROR | MSGDLVSQ(PRIORITY) |
| D5.OUT.ACK | Business acknowledgements (CorrelId = eventId) | YES | BOTHRESH 10 → D5.ERROR | |
| D5.ERROR | ACE validation rejects, central rejections, poison messages after backout | YES | – | |
| D5.DLQ | Queue-manager dead-letter queue | YES | – | `ALTER QMGR DEADQ` |

## Message conventions
- **Persistence:** every D5 message is persistent. `MQMD.Persistence = MQPER_PERSISTENT` is set in ESQL, and the MQOutput nodes also have `persistenceMode="yes"`.
- **Priority mapping:**

  | Business priority | Queue | MQMD.Priority |
  |---|---|---|
  | CRITICAL | D5.IN.CRITICAL | 9 |
  | NORMAL | D5.IN.NORMAL | 5 |
  | LOW | D5.IN.LOW | 1 |

  The MQ priority is informational. Strict ordering is enforced by the vessel dispatcher, which keeps one event in flight per vessel, so the queues rarely hold more than one message per vessel. Separate queues let operators see the backlog by priority and could be given separate consumers or instances in production.
- **CorrelId:** the 16 bytes of the eventId UUID followed by 8 zero bytes. It is set on the inbound message and copied to the ack, so a message can be traced with MQ tools.
- **Format:** `MQSTR`, CCSID 1208, JSON body.

## Transactions
- Ingest put: **outside** syncpoint (`transactionMode="no"`), so the message is committed before HTTP 202 is returned.
- Processor: the MQInput get, the central call and the D5.OUT.ACK or D5.ERROR put run under one syncpoint (`transactionMode="yes"`). See [data-flow.md](data-flow.md).

## Operating commands (read-only)
```bash
su mqm -s /bin/bash -c "echo \"DISPLAY QLOCAL('D5.*') CURDEPTH\" | /opt/mqm/bin/runmqsc D5QM"
su mqm -s /bin/bash -c "echo \"DISPLAY QSTATUS('D5.IN.*') IPPROCS\" | /opt/mqm/bin/runmqsc D5QM"   # ACE input handles
su mqm -s /bin/bash -c "/opt/mqm/samp/bin/amqsbcg D5.ERROR D5QM"                                 # browse parked messages
```
The dashboard shows live queue depths read with `runmqsc -e D5QM` (`DISPLAY QLOCAL('D5.*') CURDEPTH`).
