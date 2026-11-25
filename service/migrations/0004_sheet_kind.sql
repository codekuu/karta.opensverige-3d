-- A sheet says which source it is drawn from, so the drawing code no longer keeps a line per
-- city: 'open' is the orthophoto and OpenStreetMap, 'google' is Google's 3D tiles through the
-- builder's own Cesium ion account. It also says how it came to be: 'hand' by the admin
-- (POST /sheets), 'grid' by the tiling on the first claim there.
ALTER TABLE sheets ADD COLUMN kind TEXT NOT NULL DEFAULT 'open' CHECK (kind IN ('open', 'google'));
ALTER TABLE sheets ADD COLUMN made TEXT NOT NULL DEFAULT 'hand' CHECK (made IN ('hand', 'grid'));
UPDATE sheets SET kind = 'google' WHERE id = 'stockholm-norr';
