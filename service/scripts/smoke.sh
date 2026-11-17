#!/usr/bin/env bash
# Smoke test against a deployed rut-tjänst: health, sheets, grid, me, claim, upload, release.
# Usage: BASE=https://pixelbygget.<account>.workers.dev GH_TOKEN=<github user token> scripts/smoke.sh <sheet> <col>_<row>
# Exits non-zero on the first unexpected status. Leaves the cell free again.
set -euo pipefail

BASE="${BASE:?set BASE to the service url}"
TOKEN="${GH_TOKEN:?set GH_TOKEN to a GitHub user token}"
SHEET="${1:?sheet id, e.g. goteborg-norr}"
CELL="${2:?cell id, e.g. 64_59}"
COL="${CELL%_*}"
ROW="${CELL#*_}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

expect() { # expect <wanted status> <got status> <what>
  if [ "$1" != "$2" ]; then echo "FAIL: $3 answered $2, wanted $1"; exit 1; fi
  echo "ok: $3 ($2)"
}

status() { curl -s -o "$TMP/body" -w '%{http_code}' "$@"; }

expect 200 "$(status "$BASE/v1/health")" health
expect 200 "$(status "$BASE/v1/sheets")" sheets
expect 200 "$(status "$BASE/v1/grid/$SHEET")" "grid $SHEET"
expect 200 "$(status -H "Authorization: Bearer $TOKEN" "$BASE/v1/me")" me

expect 201 "$(status -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data "{\"sheet\":\"$SHEET\",\"col\":$COL,\"row\":$ROW}" "$BASE/v1/claim")" "claim $CELL"

# A 1024 px PNG header with a correct manifest. Not a real picture, enough for the checks.
PIPELINE="$(python3 -c "import json,sys; print(json.load(open('$TMP/body'))['sheet']['pipeline'])" 2>/dev/null || true)"
if [ -z "$PIPELINE" ]; then
  status "$BASE/v1/grid/$SHEET" >/dev/null
  PIPELINE="$(python3 -c "import json; print(json.load(open('$TMP/body'))['sheet']['pipeline'])")"
fi
python3 - "$TMP" "$PIPELINE" <<'PY'
import base64, hashlib, json, struct, sys
tmp, pipeline = sys.argv[1], sys.argv[2]
def u32(n): return struct.pack('>I', n)
ihdr = u32(1024) + u32(1024) + bytes([8, 6, 0, 0, 0])
png = b'\x89PNG\r\n\x1a\n' + u32(13) + b'IHDR' + ihdr + b'\0\0\0\0' + u32(0) + b'IEND' + b'\xaeB`\x82'
open(f'{tmp}/cell.png', 'wb').write(png)
manifest = {"pipeline": pipeline, "seed": 1, "sha256": hashlib.sha256(png).hexdigest(), "width": 1024, "height": 1024,
            "source": {"provider": "smoke", "version": "0"}, "gpu": "none", "renderedAt": "2026-01-01T00:00:00Z"}
open(f'{tmp}/manifest.b64', 'w').write(base64.b64encode(json.dumps(manifest).encode()).decode())
PY

expect 201 "$(status -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: image/png' \
  -H "X-Manifest: $(cat "$TMP/manifest.b64")" --data-binary "@$TMP/cell.png" "$BASE/v1/upload/$SHEET/$CELL")" "upload $CELL"
echo "upload answered: $(cat "$TMP/body")"

# The cell is now uploaded, so release must refuse. The admin frees it with reject.
expect 409 "$(status -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data "{\"sheet\":\"$SHEET\",\"col\":$COL,\"row\":$ROW}" "$BASE/v1/release")" "release after upload refused"
echo "done: reject $CELL as admin to free it: rutor reject $SHEET $CELL --comment smoke"
