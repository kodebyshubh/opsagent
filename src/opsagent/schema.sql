-- OpsAgent schema. Money columns are integer paise (DEC-018).
DROP TABLE IF EXISTS action_records;
DROP TABLE IF EXISTS audit_events;
DROP TABLE IF EXISTS runs;
DROP TABLE IF EXISTS memory_entries;
DROP TABLE IF EXISTS outbox;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS refunds;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  phone TEXT,
  city TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(id),
  status TEXT NOT NULL CHECK (status IN ('placed','packed','shipped','delivered','cancelled','returned')),
  total_amount_paise INTEGER NOT NULL CHECK (total_amount_paise >= 0),
  order_date TEXT NOT NULL,
  delivered_date TEXT,
  refund_status TEXT NOT NULL DEFAULT 'none' CHECK (refund_status IN ('none','partial','full'))
);
CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE TABLE order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  sku TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_paise INTEGER NOT NULL CHECK (unit_price_paise >= 0)
);
CREATE INDEX idx_items_order ON order_items(order_id);

CREATE TABLE inventory (
  sku TEXT PRIMARY KEY,
  stock INTEGER NOT NULL CHECK (stock >= 0),
  reorder_level INTEGER NOT NULL DEFAULT 5,
  warehouse TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE refunds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
  reason TEXT NOT NULL,
  run_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_refunds_order ON refunds(order_id);

CREATE TABLE tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id TEXT NOT NULL REFERENCES customers(id),
  order_id INTEGER REFERENCES orders(id),
  priority TEXT NOT NULL CHECK (priority IN ('low','normal','high','urgent')),
  status TEXT NOT NULL CHECK (status IN ('open','in_progress','resolved')),
  assignee TEXT NOT NULL DEFAULT 'agent_queue',
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id TEXT NOT NULL REFERENCES customers(id),
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  run_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE memory_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id TEXT REFERENCES customers(id),
  kind TEXT NOT NULL CHECK (kind IN ('note','interaction_summary')),
  content TEXT NOT NULL,
  run_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_memory_customer ON memory_entries(customer_id, created_at);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  parent_run_id TEXT,
  query TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','awaiting_approval','awaiting_clarification','completed','failed')),
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event_type TEXT NOT NULL,
  node TEXT,
  payload_json TEXT NOT NULL
);
CREATE INDEX idx_audit_run ON audit_events(run_id, id);

CREATE TABLE action_records (
  run_id TEXT NOT NULL,
  action_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args_json TEXT NOT NULL,
  risk TEXT NOT NULL CHECK (risk IN ('LOW','MEDIUM','HIGH')),
  status TEXT NOT NULL CHECK (status IN ('proposed','invalid','awaiting_approval','approved','rejected','skipped','running','succeeded','failed')),
  result_json TEXT,
  started_at TEXT,
  ended_at TEXT,
  PRIMARY KEY (run_id, action_id)
);
