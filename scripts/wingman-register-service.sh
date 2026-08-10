#!/usr/bin/env bash
# wingman-register-service.sh — declare the shared multi-tenant process to
# the host service registry (#288).
#
# The registry (/var/lib/common-services/registry.json, helper at
# /usr/local/bin/service-registry) records which service owns which loopback
# port and which funnel path, so two services cannot claim the same one.
#
# Until this existed, wingman was declared there by dhk/minority-report's
# deployment pack, on wingman's behalf, from a repository that cannot observe
# this one. It described two per-user processes on 8787 and 8788 long after
# wingman became a single multi-user process on 8789 — and because the check
# compared that pack's declaration against the registry the same pack had
# written, it reported healthy throughout. Meanwhile the ports and paths
# actually in use were reserved by nobody: a funnel path has no "is something
# listening" backstop, so another service could have taken '/shared' and the
# registry would have allowed it.
#
# Paths are DISCOVERED from the running funnel rather than assumed. A constant
# here could disagree with reality silently, which is precisely the failure
# this script exists to end — so it declares what is served, not what someone
# once believed was served.
#
# Idempotent: re-running re-asserts the same reservation. Safe when the
# registry helper is absent — it belongs to another tool's pack and wingman
# does not depend on it.
#
# Usage: sudo ./wingman-register-service.sh
# Env overrides: WINGMAN_SHARED_USER (default wingman-shared),
#                WINGMAN_SHARED_PORT (default 8789),
#                WINGMAN_SHARED_TAILSCALE_PATH (fallback only, when the
#                  funnel cannot be read; default /shared),
#                WINGMAN_REGISTRY_HELPER, WINGMAN_REGISTRY_PATH,
#                WINGMAN_REGISTRY_STATIC_RANGE, WINGMAN_REGISTRY_DYNAMIC_RANGE

set -euo pipefail

SERVICE_USER="${WINGMAN_SHARED_USER:-wingman-shared}"
PORT="${WINGMAN_SHARED_PORT:-8789}"
FALLBACK_PATH="${WINGMAN_SHARED_TAILSCALE_PATH:-/shared}"
DEFAULT_REGISTRY="/var/lib/common-services/registry.json"
HELPER="${WINGMAN_REGISTRY_HELPER:-/usr/local/bin/service-registry}"
REGISTRY="${WINGMAN_REGISTRY_PATH:-$DEFAULT_REGISTRY}"
STATIC_RANGE="${WINGMAN_REGISTRY_STATIC_RANGE:-8700-8799}"
DYNAMIC_RANGE="${WINGMAN_REGISTRY_DYNAMIC_RANGE:-8800-8999}"
SERVICE_ID="wingman"

say() { printf '==> %s\n' "$*"; }

# Root is required to write the real registry, which is root-owned. It is not
# required when pointed at a different file: that is an explicit choice by a
# developer or a test, and the helper will report its own permission error if
# the target turns out to be privileged after all.
if [ "$REGISTRY" = "$DEFAULT_REGISTRY" ] && [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

if [ ! -x "$HELPER" ]; then
  say "no service registry helper at $HELPER — skipping."
  say "The helper is installed by another tool's deployment pack and wingman"
  say "does not require it. Once it exists, run: sudo $0"
  exit 0
fi

discover_paths() {
  command -v tailscale >/dev/null 2>&1 || return 1
  tailscale serve status --json 2>/dev/null | python3 -c '
import json, sys

try:
    payload = json.load(sys.stdin)
except ValueError:
    raise SystemExit(1)
target = sys.argv[1]
paths = sorted(
    {
        path
        for host in (payload.get("Web") or {}).values()
        for path, handler in ((host or {}).get("Handlers") or {}).items()
        if (handler or {}).get("Proxy") == target
    }
)
if not paths:
    raise SystemExit(1)
print("\n".join(paths))
' "http://127.0.0.1:$PORT"
}

PATHS=()
while IFS= read -r line; do
  [ -n "$line" ] && PATHS+=("$line")
done < <(discover_paths || true)

if [ "${#PATHS[@]}" -eq 0 ]; then
  say "funnel not readable (or nothing mounted on $PORT) — declaring $FALLBACK_PATH"
  PATHS=("$FALLBACK_PATH")
else
  say "funnel paths serving 127.0.0.1:$PORT — ${PATHS[*]}"
fi

say "1/3 reserving the endpoint"
# --adopt-listener because the process is already running: the helper
# otherwise refuses a port something is listening on, which is the right
# default for a NEW reservation and wrong for recording an existing one.
#
# --health-url is declared now that /health names the service it belongs to
# (webui.HEALTH_SERVICE_NAME). The helper verifies health by fetching that
# URL and comparing its 'service' field against the declared name, so before
# that field existed this could only ever have reported failure — which is
# why it was left off, and what the note here said to fix first (#288).
#
# The comparison, not just a 200, is the point: a probe that accepted any
# response would call the entry healthy for whatever else ended up bound to
# this port later.
"$HELPER" --registry "$REGISTRY" \
  --static-range "$STATIC_RANGE" --dynamic-range "$DYNAMIC_RANGE" \
  reserve "$SERVICE_ID" \
  --name "Wingman" \
  --owner "$SERVICE_USER" \
  --protocol tcp \
  --address 127.0.0.1 \
  --port "$PORT" \
  --unit wingman-mcp.service \
  --health-url "http://127.0.0.1:$PORT/health" \
  --health-service wingman \
  --source dhk/wingman \
  --adopt-listener

say "2/3 reserving the funnel route(s)"
ROUTE_ARGS=()
for path in "${PATHS[@]}"; do
  ROUTE_ARGS+=(--path "$path")
done
"$HELPER" --registry "$REGISTRY" \
  --static-range "$STATIC_RANGE" --dynamic-range "$DYNAMIC_RANGE" \
  reserve-route "$SERVICE_ID" \
  --host tailscale-self \
  --https-port 443 \
  "${ROUTE_ARGS[@]}" \
  --mode funnel \
  --target "http://127.0.0.1:$PORT"

say "3/3 verifying the ledger records what was declared"
# Not ceremony. A helper predating multi-path support (dhk/minority-report#21)
# accepts repeated --path and silently keeps only the last one, which would
# leave every other path unclaimed while this script reported success — the
# same silent disagreement the discovery step above exists to prevent.
python3 - "$REGISTRY" "$SERVICE_ID" "$PORT" "${PATHS[@]}" <<'PY'
import json
import sys

registry, service_id, port, *expected = sys.argv[1:]
try:
    with open(registry, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError) as exc:
    raise SystemExit(f"ERROR: cannot read {registry}: {exc}")

entry = (data.get("services") or {}).get(service_id) or {}
routes = entry.get("routes")
if not isinstance(routes, list):
    single = entry.get("route")
    routes = [single] if isinstance(single, dict) else []
actual_paths = [route.get("path") for route in routes if isinstance(route, dict)]
actual_port = (entry.get("endpoint") or {}).get("port")

problems = []
if str(actual_port) != str(port):
    problems.append(f"port recorded as {actual_port!r}, declared {port!r}")
if actual_paths != expected:
    problems.append(f"paths recorded as {actual_paths!r}, declared {expected!r}")

if problems:
    print("ERROR: the registry does not match what was just declared:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "  A registry helper predating multi-path support keeps only the last\n"
        "  --path. Upgrade it (dhk/minority-report#21) and re-run.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

say "Declared $SERVICE_ID — 127.0.0.1:$PORT serving ${PATHS[*]}"
