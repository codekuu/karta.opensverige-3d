#!/usr/bin/env bash
# A local Pixelbygget service for code contributors. No GitHub app, no Cloudflare account and
# no real token: two made-up users are put straight into the local KV cache that the service
# reads before it would ask GitHub.
#
#   scripts/local.sh setup   once: local database, the two users, .dev.vars
#   npx wrangler@4.163.0 dev --local --port 8787     in another terminal, keep it running
#   scripts/local.sh sheet   once the service answers: opens goteborg-norr with every cell free
#
# Then, from cli/:
#   PIXELBYGGET_URL=http://127.0.0.1:8787 PIXELBYGGET_HOME=../work/local-home python -m rutor whoami
# with the builder token from `scripts/local.sh token` in work/local-home/token.
set -euo pipefail
cd "$(dirname "$0")/.."

WRANGLER="npx wrangler@4.163.0"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
BASE="${BASE:-http://127.0.0.1:8787}"
ADMIN_TOKEN="local-admin-token-not-secret"
BUILDER_TOKEN="local-builder-token-not-secret"

hash() {
  "$PYTHON" -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"
}

case "${1:-}" in
  setup)
    if [ ! -f .dev.vars ]; then
      printf 'ADMIN_GITHUB_ID="1"\nPUBLIC_CACHE_SECONDS="0"\n' > .dev.vars
    fi
    $WRANGLER d1 migrations apply pixelbygget --local
    $WRANGLER kv key put --binding GH --local "gh:$(hash "$ADMIN_TOKEN")" '{"id":1,"login":"local-admin"}'
    $WRANGLER kv key put --binding GH --local "gh:$(hash "$BUILDER_TOKEN")" '{"id":2,"login":"local-builder"}'
    echo "setup: done. Start the service: $WRANGLER dev --local --port 8787"
    ;;
  sheet)
    "$PYTHON" - "$BASE" "$ADMIN_TOKEN" <<'PY'
import json, sys, urllib.error, urllib.request

base, token = sys.argv[1], sys.argv[2]
cols, rows = 130, 118
sheet = {
    "id": "goteborg-norr", "city": "goteborg", "name": "Göteborg", "cols": cols, "rows": rows,
    "origin_lat": 57.707, "origin_lon": 11.967, "x_meters": -16384, "y_meters": 13312,
    "ground_height": 40, "dzi_levels": 18, "pipeline": "2026.11-a", "state": "open", "queue_order": 1,
    "mask": [[c, r] for r in range(rows) for c in range(cols)],
}
request = urllib.request.Request(
    base + "/v1/sheets", data=json.dumps(sheet).encode(), method="POST",
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        print("sheet:", response.status, response.read().decode())
except urllib.error.HTTPError as exc:
    body = exc.read().decode()
    if exc.code == 409:
        print("sheet: goteborg-norr is already open")
    else:
        sys.exit(f"sheet: {exc.code} {body}")
PY
    ;;
  token)
    echo "$BUILDER_TOKEN"
    ;;
  *)
    echo "usage: scripts/local.sh setup | sheet | token" >&2
    exit 1
    ;;
esac
