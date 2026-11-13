-- D5 central office database (SQLite, WAL, synchronous=FULL)
CREATE TABLE IF NOT EXISTS records (            -- Office AMOS: current state of every operational record
  record_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, vessel_id TEXT NOT NULL, priority TEXT NOT NULL,
  version INTEGER NOT NULL, payload TEXT NOT NULL, source_system TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
  conflict_open INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_records_vessel ON records(vessel_id, record_type);

CREATE TABLE IF NOT EXISTS record_versions (    -- full history: every version ever committed centrally
  record_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, changed_by TEXT NOT NULL,
  change_source TEXT NOT NULL, event_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(record_id, version));

CREATE TABLE IF NOT EXISTS event_ledger (       -- idempotency key = event_id; one row per distinct event
  event_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, vessel_id TEXT NOT NULL, record_type TEXT NOT NULL,
  event_type TEXT NOT NULL, priority TEXT NOT NULL, sequence_number INTEGER NOT NULL,
  event_created_at TEXT NOT NULL, status TEXT NOT NULL,          -- PROCESSED | REJECTED
  outcome TEXT, detail TEXT, transaction_id TEXT, mq_queue TEXT, mq_backout_count INTEGER,
  first_received_at TEXT NOT NULL, last_received_at TEXT NOT NULL, received_count INTEGER NOT NULL DEFAULT 1,
  processed_at TEXT, processing_order INTEGER, ack_recorded_at TEXT, ack_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_ledger_vessel ON event_ledger(vessel_id, processing_order);

CREATE TABLE IF NOT EXISTS business_transactions (  -- exactly one per processed event_id (UNIQUE)
  transaction_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, record_id TEXT NOT NULL,
  vessel_id TEXT NOT NULL, action TEXT NOT NULL, version_after INTEGER, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS duplicate_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, vessel_id TEXT, received_at TEXT NOT NULL,
  mq_queue TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS conflicts (
  conflict_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, vessel_id TEXT NOT NULL, event_id TEXT NOT NULL,
  detected_at TEXT NOT NULL, status TEXT NOT NULL,                -- AUTO_RESOLVED | OPEN | RESOLVED
  base_version INTEGER, office_version INTEGER, vessel_version INTEGER,
  base_payload TEXT, office_payload TEXT, vessel_payload TEXT, merged_payload TEXT,
  field_decisions TEXT NOT NULL, final_payload TEXT, final_version INTEGER,
  resolved_at TEXT, resolved_by TEXT, resolution_note TEXT);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  entity TEXT, entity_id TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_log(entity, entity_id);

CREATE TABLE IF NOT EXISTS downlink_feed (      -- office -> vessel record changes (pulled by edge over the link)
  id INTEGER PRIMARY KEY AUTOINCREMENT, vessel_id TEXT NOT NULL, record_id TEXT NOT NULL, version INTEGER NOT NULL,
  payload TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_downlink ON downlink_feed(vessel_id, id);

CREATE TABLE IF NOT EXISTS vessels (
  vessel_id TEXT PRIMARY KEY, name TEXT NOT NULL, vessel_group TEXT NOT NULL, edge_port INTEGER,
  last_heartbeat_at TEXT, agent_version TEXT, config_version INTEGER, heartbeat TEXT);

CREATE TABLE IF NOT EXISTS config_versions (
  version INTEGER PRIMARY KEY, config TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  notes TEXT, target TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config_deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, config_version INTEGER NOT NULL, vessel_id TEXT NOT NULL,
  status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_packages (
  version TEXT PRIMARY KEY, manifest TEXT NOT NULL, package BLOB NOT NULL, sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, notes TEXT);
CREATE TABLE IF NOT EXISTS version_deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, vessel_id TEXT NOT NULL, action TEXT NOT NULL,  -- UPDATE | ROLLBACK
  target_version TEXT, status TEXT NOT NULL, detail TEXT, from_version TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
