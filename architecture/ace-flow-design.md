# D5 – IBM ACE Flow Design

**Runtime:** IBM App Connect Enterprise 13.0.9.0 standalone integration server **IS_D5_EDGE**.
- Workdir: `runtime/IS_D5_EDGE`
- Admin REST: 7614 (HTTPS)
- HTTP listener: 7840
- Queue manager: D5QM (local bindings)
- Runs as `aceadmin` in the transient systemd unit `d5-ace`
- Start/stop: `ace/deployment/is-d5-edge.sh`

**Application:** `D5_EdgeSync`.
- Project: `ace/projects/D5_EdgeSync`
- BAR: `ace/bar/D5_EdgeSync.bar`

**Build and deploy (headless):** `ace/deployment/build-deploy.sh`. It runs:
1. `ace/flows/gen_flows.py`, which writes the `.msgflow` files.
2. `ibmint package`.
3. `ibmint deploy --output-port 7614 --https --insecure`.

The node attributes and enum values used by the generator were checked against `common/schemas/MessageFlow/MessageFlow.xsd` of this installation. To open the project in the App Connect Toolkit, use File → Import → Existing Projects into Workspace and select `ace/projects/D5_EdgeSync`.

ESQL: [`ace/projects/D5_EdgeSync/D5_EdgeSync.esql`](../ace/projects/D5_EdgeSync/D5_EdgeSync.esql).

## Flows

### D5_EventIngest — `POST /d5/v1/events`
`HTTPInput (JSON)` → `Compute D5_ValidateAndRoute` → `MQOutput (destination list, persistent, no syncpoint)` → `Compute D5_BuildIngestReply` → `HTTPReply`

- Validation checks:
  - eventId is a lowercase UUID.
  - recordId and vesselId are present.
  - recordType and eventType come from the allowed sets.
  - sequenceNumber and version are present.
  - payload is present.
  - priority is CRITICAL, NORMAL or LOW.
- A valid event goes to `D5.IN.<priority>` with MQMD priority 9/5/1, CorrelId = eventId and persistent delivery. The reply is **202** with `{status: ACCEPTED, queue, mqPriority, mqMsgId, note: "NOT a business acknowledgement"}`.
- An invalid event is put to `D5.ERROR` with the reasons, and the reply is **400** `{status: REJECTED, errors}`.

### D5_EventProcessor — MQ `D5.IN.CRITICAL | D5.IN.NORMAL | D5.IN.LOW`
`3 × MQInput (JSON, syncpoint)` → `Compute D5_PrepareCentralCall` → `HTTPRequest POST /internal/process-event`
- `out` → `Compute D5_BuildBusinessAck` → `MQOutput D5.OUT.ACK (syncpoint, persistent)`
- `error` → `Compute D5_HandleCentralError`
  - 4xx: the original event goes to `D5.ERROR` and a rejection ack to `D5.OUT.ACK`.
  - 5xx: `THROW`, which rolls back.
- `failure` is unconnected, so the flow rolls back. MQ redelivers, and after BOTHRESH(3) ACE moves the message to `D5.ERROR`.

Headers sent to central: `X-D5-MQ-Queue`, `X-D5-MQ-Backout` (the MQMD backout count).

### D5_AckDispatcher — MQ `D5.OUT.ACK`
`MQInput (JSON, syncpoint)` → `Compute D5_PrepareAckCall` → `HTTPRequest POST /internal/ack`.

The error and failure terminals are deliberately unconnected, so a failure rolls back and is retried. After BOTHRESH(10) the message moves to D5.ERROR.

### D5_EventStatus — `GET /d5/v1/event-status?eventId=<uuid>`
`HTTPInput (BLOB)` → `Compute D5_PrepareStatusCall` → `HTTPRequest GET /internal/event-status`
- `out` and `error` → `Compute D5_RelayStatus` → `HTTPReply`
- `failure` → `Compute D5_StatusUnavailable` (503)

> Verified on this ACE build: the query string is in `InputRoot.HTTPInputHeader."X-Query-String"`. `LocalEnvironment.HTTP.Input.QueryString` was **not** populated. Only UUID characters are forwarded, which prevents URL injection.

## User-defined property
`CentralBaseUrl` (default `http://127.0.0.1:8500`) on D5_EventProcessor, D5_AckDispatcher and D5_EventStatus. It can be overridden per environment with a BAR override, without changing code.

## Verifying in ACE
```bash
curl -sk https://127.0.0.1:7614/apiv2/applications/D5_EdgeSync/messageflows/D5_EventProcessor | grep -E 'isRunning|state'
grep -E 'BIP[0-9]+[EW]' logs/ace-IS_D5_EDGE.console.log | tail
```
