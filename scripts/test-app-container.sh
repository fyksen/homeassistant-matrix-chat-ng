#!/bin/sh
set -eu
ENGINE="${CONTAINER_ENGINE:-docker}"
IMAGE="${1:-matrix-ng-app:ci}"
VOLUME="matrix-ng-app-smoke-$$"
CONTAINER="matrix-ng-app-smoke-$$"
FIXTURE="$(mktemp -d)"
cleanup() {
  "$ENGINE" rm -f "$CONTAINER" >/dev/null 2>&1 || true
  "$ENGINE" volume rm "$VOLUME" >/dev/null 2>&1 || true
  rm -f "$FIXTURE/bridge" "$FIXTURE/options.json"
  rmdir "$FIXTURE" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
cat > "$FIXTURE/options.json" <<'JSON'
{"user_id":"@smoke:example.org","rooms":[],"listen_for_commands":true}
JSON
cat > "$FIXTURE/bridge" <<'PY'
#!/usr/bin/python3
import json, os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
config = json.load(open(os.environ["MATRIX_NG_CONFIG"]))
assert os.getuid() == 10001, "Bridge did not drop root"
assert "SUPERVISOR_TOKEN" not in os.environ, "Supervisor token leaked to bridge"
state = Path(config["storage_path"])
state.mkdir(parents=True, exist_ok=True)
(state / "smoke-state").write_text("preserved")
print("Non-root app bridge started without Supervisor credentials", flush=True)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ready":true}')
    def log_message(self, *args): pass
HTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
PY
chmod 755 "$FIXTURE/bridge"
"$ENGINE" volume create "$VOLUME" >/dev/null
"$ENGINE" run --rm --entrypoint sh -v "$VOLUME:/data" -v "$FIXTURE/options.json:/tmp/options:ro" "$IMAGE" -c 'cp /tmp/options /data/options.json' >/dev/null
"$ENGINE" run -d --name "$CONTAINER" --network none -e SUPERVISOR_TOKEN=dummy-supervisor-token \
  -v "$VOLUME:/data" -v "$FIXTURE/bridge:/usr/local/bin/matrix-ng-bridge:ro" "$IMAGE" >/dev/null
# Readiness wait for an app we just started, not a real Matrix account/network test.
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if "$ENGINE" exec "$CONTAINER" python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8099/health", timeout=1)' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
"$ENGINE" logs "$CONTAINER" 2>&1 | grep -q 'Non-root app bridge started without Supervisor credentials'
BEFORE="$("$ENGINE" exec "$CONTAINER" python3 -c 'import hashlib; print(hashlib.sha256(open("/data/matrix-ng/api-token","rb").read()).hexdigest())')"
"$ENGINE" stop --time 5 "$CONTAINER" >/dev/null
"$ENGINE" rm "$CONTAINER" >/dev/null
"$ENGINE" run --rm --entrypoint python3 -v "$VOLUME:/data" "$IMAGE" -c 'from pathlib import Path; assert Path("/data/matrix-ng/sdk/smoke-state").read_text() == "preserved"'
AFTER="$("$ENGINE" run --rm --entrypoint python3 -v "$VOLUME:/data" "$IMAGE" -c 'import hashlib; print(hashlib.sha256(open("/data/matrix-ng/api-token","rb").read()).hexdigest())')"
test "$BEFORE" = "$AFTER"
echo "Supervisor bootstrap, privilege drop, token isolation and persistent-data smoke test passed"
