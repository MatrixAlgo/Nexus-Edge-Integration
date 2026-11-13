-- D5 edge runtime local store (one SQLite file per vessel; WAL, synchronous=FULL)
CREATE TABLE IF NOT EXISTS operational_records (
  record_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, vessel_id TEXT NOT NULL, source_system TEXT NOT NULL,
  priority TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
  sync_status TEXT NOT NULL DEFAULT 'PENDING');            -- PENDING | SYNCED | FROM_OFFICE
CREATE INDEX IF NOT EXISTS ix_rec_type ON operational_records(record_type);

CREATE TABLE IF NOT EXISTS outbound_events (               -- transactional outbox; rows are never deleted before ACK
  event_id TEXT PRIMARY KEY, sequence_number INTEGER NOT NULL UNIQUE, record_id TEXT NOT NULL,
  record_type TEXT NOT NULL, event_type TEXT NOT NULL, priority TEXT NOT NULL, priority_rank INTEGER NOT NULL,
  version INTEGER NOT NULL, base_version INTEGER, event_json TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL,          -- PENDING | SENDING | ACCEPTED (in MQ, awaiting business ack) | ACKNOWLEDGED | FAILED
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, accepted_at TEXT, acked_at TEXT,
  delivery_order INTEGER, last_error TEXT, central_outcome TEXT, transaction_id TEXT, mq_queue TEXT,
  replay_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_out_dispatch ON outbound_events(status, priority_rank, sequence_number);
CREATE INDEX IF NOT EXISTS ix_out_record ON outbound_events(record_id, sequence_number);

CREATE TABLE IF NOT EXISTS delivery_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, attempt_no INTEGER NOT NULL, phase TEXT NOT NULL,
  started_at TEXT NOT NULL, duration_ms INTEGER, result TEXT NOT NULL, http_status INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_att_event ON delivery_attempts(event_id);

CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sync_sessions (                 -- one row per outage -> reconnect -> drain cycle
  id INTEGER PRIMARY KEY AUTOINCREMENT, offline_since TEXT, reconnected_at TEXT NOT NULL, offline_duration_sec REAL,
  backlog_at_start INTEGER NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
  drained_at TEXT, delivery_sequence TEXT NOT NULL DEFAULT '[]');

CREATE TABLE IF NOT EXISTS edge_configuration (
  version INTEGER PRIMARY KEY, config TEXT NOT NULL, received_at TEXT NOT NULL, applied_at TEXT,
  status TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, detail TEXT);

CREATE TABLE IF NOT EXISTS edge_versions (
  version TEXT PRIMARY KEY, sha256 TEXT, manifest TEXT, staged_path TEXT, staged_at TEXT, activated_at TEXT,
  status TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, previous_version TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS applied_downlink (id INTEGER PRIMARY KEY, record_id TEXT, version INTEGER, reason TEXT,
  applied_at TEXT NOT NULL, outcome TEXT NOT NULL);
