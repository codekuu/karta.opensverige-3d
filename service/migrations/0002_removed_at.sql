-- A live cell the admin removes is marked here until the intake has blanked its tiles.
ALTER TABLE cells ADD COLUMN removed_at TEXT;
