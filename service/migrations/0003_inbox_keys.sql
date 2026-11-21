-- Each upload lives under its own inbox key (cell plus the start of its sha256), so a lost race,
-- a slow upload or a later owner never deletes another upload. The cell remembers the key of
-- its current upload; rejected uploads wait in purge until their objects may go.
ALTER TABLE cells ADD COLUMN inbox_key TEXT;
CREATE TABLE purge (
  key TEXT PRIMARY KEY,
  due TEXT NOT NULL
);
CREATE INDEX purge_due ON purge (due);
