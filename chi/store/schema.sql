CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  problem TEXT NOT NULL,
  fleet_config_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  strategy_hash TEXT,
  island INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  owner_id TEXT,
  lease_expires_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  ladder_rung INTEGER NOT NULL DEFAULT 0,
  parent_task_id TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  spec_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  agent_id TEXT,
  type TEXT NOT NULL,
  task_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  cost_usd REAL NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS experiments (
  code_hash TEXT PRIMARY KEY,
  config_hash TEXT,
  run_id TEXT NOT NULL,
  task_id TEXT,
  strategy TEXT,
  island INTEGER NOT NULL DEFAULT 0,
  author TEXT,
  eval_tier TEXT NOT NULL DEFAULT 'proxy',
  correct INTEGER NOT NULL,
  seeds_passed_json TEXT NOT NULL DEFAULT '[]',
  score_metric TEXT,
  score_value REAL,
  noise_std REAL,
  parent_code_hash TEXT,
  artifacts_path TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS negative_ledger (
  neg_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  approach_class TEXT NOT NULL,
  summary TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  ruled_out_scope TEXT NOT NULL DEFAULT '',
  confidence TEXT NOT NULL DEFAULT 'medium',
  experiment_context_json TEXT NOT NULL DEFAULT '{}',
  authored_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS challenges (
  challenge_id TEXT PRIMARY KEY,
  neg_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  distinguishing_hypothesis TEXT NOT NULL,
  outcome TEXT NOT NULL DEFAULT 'pending',
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  agent_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  adapter TEXT NOT NULL,
  model TEXT NOT NULL,
  workdir TEXT,
  started_at TEXT NOT NULL,
  last_heartbeat_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  PRIMARY KEY (agent_id, run_id)
);

CREATE TABLE IF NOT EXISTS budgets (
  scope TEXT NOT NULL,
  run_id TEXT NOT NULL,
  cap_usd REAL NOT NULL,
  spent_usd REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (scope, run_id)
);

CREATE INDEX IF NOT EXISTS idx_events_run_type ON events (run_id, type);
CREATE INDEX IF NOT EXISTS idx_tasks_run_status ON tasks (run_id, status);
CREATE INDEX IF NOT EXISTS idx_experiments_run ON experiments (run_id);
