-- Pixelbygget: sheets, cells, users and the event log.
-- Timestamps are ISO 8601 UTC strings. States are the spec's words.

CREATE TABLE sheets (
  id TEXT PRIMARY KEY,
  city TEXT NOT NULL,
  name TEXT NOT NULL,
  cols INTEGER NOT NULL,
  rows INTEGER NOT NULL,
  origin_lat REAL NOT NULL,
  origin_lon REAL NOT NULL,
  x_meters REAL NOT NULL,
  y_meters REAL NOT NULL,
  ground_height REAL NOT NULL,
  dzi_levels INTEGER NOT NULL,
  pipeline TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued', 'open', 'done')),
  queue_order INTEGER NOT NULL,
  rev INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE cells (
  sheet TEXT NOT NULL REFERENCES sheets(id),
  col INTEGER NOT NULL,
  row INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('ute', 'fri', 'last', 'vantar', 'godkand', 'live')),
  owner_id INTEGER,
  owner_login TEXT,
  locked_at TEXT,
  expires_at TEXT,
  uploaded_at TEXT,
  approved_at TEXT,
  live_at TEXT,
  rejected_at TEXT,
  bytes INTEGER,
  sha256 TEXT,
  comment TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (sheet, col, row)
);
CREATE INDEX cells_state ON cells(sheet, state);
CREATE INDEX cells_owner ON cells(owner_id, state);

CREATE TABLE users (
  github_id INTEGER PRIMARY KEY,
  login TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  approved_count INTEGER NOT NULL DEFAULT 0,
  trusted INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  github_id INTEGER,
  login TEXT,
  action TEXT NOT NULL,
  sheet TEXT,
  col INTEGER,
  row INTEGER,
  detail TEXT
);
CREATE INDEX events_at ON events(at);
