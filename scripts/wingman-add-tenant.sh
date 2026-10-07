#!/usr/bin/env bash
# wingman-add-tenant.sh <slug> — provision one tenant's workspace under
# the shared multi-tenant process (RFC-048) and register them, issuing
# their first connector URL. Run after wingman-provision-shared.sh.
#
# Idempotent: skips workspace init if the tenant's data_dir already has
# a wingman.db (a workspace created by hand, e.g. via 'wingman init',
# before this script existed), and refuses if the slug is already in
# the registry rather than duplicating the entry.
#
# Usage: sudo ./wingman-add-tenant.sh <slug> [--telemetry|--no-telemetry]
#        [--oauth-issuer URL --oauth-subject SUBJECT --oauth-identities PATH]
#
# The telemetry decision (RFC-023's local usage journal) is taken here
# rather than inherited. Default-off is right for someone installing on
# their own machine — their machine, their choice. It is the wrong thing
# to inherit SILENTLY when you are provisioning a workspace on somebody
# else's behalf, because then nobody chose at all: four tenants existed
# on this box recording nothing, and nobody found out until a question
# came up that the journal would have answered (#299).
#
# With no flag and a terminal, it asks. With no flag and no terminal it
# fails rather than guessing, because guessing is what produced that.

set -euo pipefail

SERVICE_USER="${WINGMAN_SHARED_USER:-wingman-shared}"
PORT="${WINGMAN_SHARED_PORT:-8789}"
TAILSCALE_PATH="${WINGMAN_SHARED_TAILSCALE_PATH:-/shared}"
REGISTRY_PATH="${WINGMAN_SHARED_REGISTRY:-/etc/wingman/tenants.toml}"
OAUTH_SERVICE_CONFIG="${WINGMAN_SHARED_OAUTH_CONFIG:-/etc/wingman/oauth.env}"
SLUG="${1:?usage: $0 <slug> [--telemetry|--no-telemetry]}"
shift
TELEMETRY=""
OAUTH_ISSUER=""
OAUTH_SUBJECT=""
OAUTH_IDENTITIES=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --telemetry|--no-telemetry) TELEMETRY="$1"; shift ;;
    --oauth-issuer) OAUTH_ISSUER="${2:?--oauth-issuer needs a URL}"; shift 2 ;;
    --oauth-subject) OAUTH_SUBJECT="${2:?--oauth-subject needs a subject}"; shift 2 ;;
    --oauth-identities) OAUTH_IDENTITIES="${2:?--oauth-identities needs a path}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
oauth_fields=0
[ -n "$OAUTH_ISSUER" ] && oauth_fields=$((oauth_fields + 1))
[ -n "$OAUTH_SUBJECT" ] && oauth_fields=$((oauth_fields + 1))
[ -n "$OAUTH_IDENTITIES" ] && oauth_fields=$((oauth_fields + 1))
if [ "$oauth_fields" -ne 0 ] && [ "$oauth_fields" -ne 3 ]; then
  echo "OAuth provisioning needs --oauth-issuer, --oauth-subject and --oauth-identities together." >&2
  exit 2
fi

say() { printf '==> %s\n' "$*"; }

validate_oauth_identities_path() {
  [ "$oauth_fields" -eq 3 ] || return 0
  if ! sudo -iu "$SERVICE_USER" python3 - "$OAUTH_IDENTITIES" <<'PY'
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()

def metadata(candidate: Path):
    try:
        return candidate.stat()
    except FileNotFoundError:
        return None
    except PermissionError:
        print(
            f"Permission denied while examining OAuth identity-map path: {candidate}. "
            "Nothing was created or changed.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except OSError as exc:
        print(
            f"Cannot examine OAuth identity-map path {candidate}: {exc}. "
            "Nothing was created or changed.",
            file=sys.stderr,
        )
        raise SystemExit(1)

path_metadata = metadata(path)
if path_metadata is not None:
    if not stat.S_ISREG(path_metadata.st_mode):
        print(f"OAuth identity-map path is not a regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    if not os.access(path, os.R_OK | os.W_OK):
        print(f"OAuth identity-map file is not readable and writable: {path}", file=sys.stderr)
        raise SystemExit(1)

parent = path.parent
parent_metadata = metadata(parent)
while parent_metadata is None and parent != parent.parent:
    parent = parent.parent
    parent_metadata = metadata(parent)
if (
    parent_metadata is None
    or not stat.S_ISDIR(parent_metadata.st_mode)
    or not os.access(parent, os.W_OK | os.X_OK)
):
    print(
        f"OAuth identity-map parent is not a writable directory: {parent}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
  then
    echo "OAuth identity-map path '$OAUTH_IDENTITIES' is unusable by service account '$SERVICE_USER'. Use a readable file (if it exists) in a parent directory that account can write." >&2
    exit 1
  fi
}

validate_oauth_service() {
  [ "$oauth_fields" -eq 3 ] || return 0
  if [ ! -f "$OAUTH_SERVICE_CONFIG" ]; then
    echo "shared service is not configured for OAuth: no $OAUTH_SERVICE_CONFIG. Run wingman-provision-shared.sh with all WINGMAN_SHARED_OAUTH_* settings first." >&2
    exit 1
  fi
  local values
  if ! values="$(python3 - "$OAUTH_SERVICE_CONFIG" "$OAUTH_ISSUER" "$OAUTH_IDENTITIES" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
requested_issuer = sys.argv[2]
requested_identities = Path(sys.argv[3]).expanduser().resolve()
values = {}
for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    key, separator, value = line.partition("=")
    if not separator:
        print(f"malformed OAuth service configuration line: {line!r}", file=sys.stderr)
        raise SystemExit(1)
    values[key] = value

required = (
    "WINGMAN_OAUTH_ISSUER",
    "WINGMAN_OAUTH_AUDIENCE",
    "WINGMAN_OAUTH_JWKS_URI",
    "WINGMAN_OAUTH_IDENTITIES",
)
missing = [key for key in required if not values.get(key)]
if missing:
    print("OAuth service configuration is incomplete: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
if values["WINGMAN_OAUTH_ISSUER"] != requested_issuer:
    print("OAuth tenant issuer does not match the shared service configuration", file=sys.stderr)
    raise SystemExit(1)
if Path(values["WINGMAN_OAUTH_IDENTITIES"]).expanduser().resolve() != requested_identities:
    print("OAuth tenant identity map does not match the shared service configuration", file=sys.stderr)
    raise SystemExit(1)
print(values["WINGMAN_OAUTH_AUDIENCE"])
PY
)"; then
    echo "shared service OAuth configuration does not match this tenant request." >&2
    exit 1
  fi

  local metadata
  if ! metadata="$(curl --fail --silent --show-error \
    "http://127.0.0.1:$PORT/.well-known/oauth-protected-resource/mcp")"; then
    echo "shared service OAuth metadata is not live on port $PORT; no tenant state was created." >&2
    exit 1
  fi
  if ! python3 - "$values" "$OAUTH_ISSUER" "$metadata" <<'PY'
import json
import sys

audience, issuer, raw = sys.argv[1:]
try:
    metadata = json.loads(raw)
except json.JSONDecodeError as exc:
    print(f"shared service returned malformed OAuth metadata: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc
if metadata.get("resource") != audience or issuer not in metadata.get("authorization_servers", []):
    print("shared service OAuth metadata does not match its saved configuration", file=sys.stderr)
    raise SystemExit(1)
PY
  then
    echo "shared service OAuth route is not ready; no tenant state was created." >&2
    exit 1
  fi
}

# Asked before the workspace exists, so a "no" costs nothing to honour.
resolve_telemetry() {
  [ -n "$TELEMETRY" ] && return 0
  if [ ! -t 0 ]; then
    echo "no terminal to ask on: pass --telemetry or --no-telemetry explicitly." >&2
    exit 2
  fi
  cat <<'PROMPT'

Turn on the usage journal for this tenant?

  It records their commands and tool calls, with arguments and results,
  into their own workspace. It is LOCAL ONLY — nothing is transmitted
  anywhere, ever. It is what makes "which features do people actually
  use" answerable, and it can be changed later at any time with
  'wingman telemetry on|off'.

  You are deciding for somebody else. Tell them either way.

PROMPT
  printf 'Enable telemetry for %s? [y/N] ' "$SLUG"
  read -r answer
  case "$answer" in
    y|Y|yes|YES) TELEMETRY="--telemetry" ;;
    *) TELEMETRY="--no-telemetry" ;;
  esac
}

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0 $SLUG" >&2
  exit 1
fi

validate_oauth_identities_path
validate_oauth_service

if [ ! -f "$REGISTRY_PATH" ]; then
  echo "no registry at $REGISTRY_PATH — run wingman-provision-shared.sh first" >&2
  exit 1
fi

if grep -q "slug = \"$SLUG\"" "$REGISTRY_PATH"; then
  echo "tenant '$SLUG' is already in the registry ($REGISTRY_PATH) — nothing to add." >&2
  exit 1
fi

DATA_DIR="/home/$SERVICE_USER/tenants/$SLUG"

resolve_telemetry

RESERVATION=""
release_reservation() {
  [ -n "$RESERVATION" ] || return 0
  sudo -iu "$SERVICE_USER" env \
    PATH="/home/$SERVICE_USER/.local/bin:$PATH" \
    wingman tenant oauth-bind "$SLUG" \
      --issuer "$OAUTH_ISSUER" \
      --subject "$OAUTH_SUBJECT" \
      --identities "$OAUTH_IDENTITIES" \
      --release-reservation "$RESERVATION" >/dev/null 2>&1 || true
}
trap release_reservation EXIT

# The only unbounded operator interaction is complete. Atomically reserve the
# identity before creating tenant state; the final bind consumes it.
if [ "$oauth_fields" -eq 3 ]; then
  say "reserving the trusted OAuth identity"
  RESERVATION="$(sudo -iu "$SERVICE_USER" env \
    PATH="/home/$SERVICE_USER/.local/bin:$PATH" \
    wingman tenant oauth-bind "$SLUG" \
      --issuer "$OAUTH_ISSUER" \
      --subject "$OAUTH_SUBJECT" \
      --identities "$OAUTH_IDENTITIES" \
      --reserve)"
fi

if [ -f "$DATA_DIR/wingman.db" ]; then
  say "workspace for '$SLUG' already exists at $DATA_DIR — skipping init"
else
  say "initializing workspace for '$SLUG'"
  sudo -iu "$SERVICE_USER" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; WINGMAN_DATA_DIR=$DATA_DIR wingman init"
fi

if [ "$TELEMETRY" = "--telemetry" ]; then
  say "turning the usage journal ON for '$SLUG'"
  sudo -iu "$SERVICE_USER" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; WINGMAN_DATA_DIR=$DATA_DIR wingman telemetry on"
else
  say "leaving the usage journal OFF for '$SLUG' (wingman telemetry on, to change)"
fi

# Workspace initialization is not interactive, but it can still be slow. Do
# not append a registry row unless this process still owns the identity.
if [ "$oauth_fields" -eq 3 ]; then
  say "renewing the trusted OAuth identity reservation"
  sudo -iu "$SERVICE_USER" env \
    PATH="/home/$SERVICE_USER/.local/bin:$PATH" \
    wingman tenant oauth-bind "$SLUG" \
      --issuer "$OAUTH_ISSUER" \
      --subject "$OAUTH_SUBJECT" \
      --identities "$OAUTH_IDENTITIES" \
      --renew-reservation "$RESERVATION"
fi

say "registering '$SLUG' in $REGISTRY_PATH"
cat >> "$REGISTRY_PATH" <<EOF

[[tenant]]
slug = "$SLUG"
data_dir = "$DATA_DIR"
EOF

if [ "$oauth_fields" -eq 3 ]; then
  say "binding the trusted OAuth identity (no legacy URL token is minted)"
  sudo -iu "$SERVICE_USER" env \
    PATH="/home/$SERVICE_USER/.local/bin:$PATH" \
    wingman tenant oauth-bind "$SLUG" \
      --issuer "$OAUTH_ISSUER" \
      --subject "$OAUTH_SUBJECT" \
      --identities "$OAUTH_IDENTITIES" \
      --registry "$REGISTRY_PATH" \
      --reservation "$RESERVATION"
  RESERVATION=""
else
  say "issuing first token and reloading the running server"
  # --tunnel-prefix matches this same script's tailscale mount (see
  # wingman-provision-shared.sh's '--set-path'): the printed tunnel URL
  # needs it even though the shared process's own local bind never does.
  sudo -iu "$SERVICE_USER" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; wingman tenant rotate-token $SLUG --port $PORT --tunnel-prefix $TAILSCALE_PATH"
fi

# A tenant reaches no metered key until somebody decides which of the two
# ways they get one (#514, RFC-080): their own, or yours. This script writes
# neither, and until #514 there was nothing that said so — every tenant it
# has ever created started life with local tools working and every
# model-backed tool failing, which is how it was reported from the hosted
# instance. The workspace is not finished until this is settled, so say
# which way is still open rather than printing a URL and stopping.
if [ -s "$DATA_DIR/keys.env" ] && grep -q '^ANTHROPIC_API_KEY=.' "$DATA_DIR/keys.env"; then
  say "'$SLUG' has their own ANTHROPIC_API_KEY in $DATA_DIR/keys.env"
elif grep -A2 "slug = \"$SLUG\"" "$REGISTRY_PATH" | grep -q '^funded = true'; then
  say "'$SLUG' is marked funded — metered calls fall back to your declared key"
else
  if [ "$oauth_fields" -eq 3 ]; then
    cat <<NOTE

==> OAuth-only tenant can make NO model call yet, so values, assessments
    and briefs will fail while everything that only reads local data works.

    Self-funded browser key entry is not available for OAuth-only tenants yet.
    Do not send them to Manage -> Keys: this OAuth slice authenticates MCP,
    not the browser settings page.

    To fund '$SLUG', put the key in a tier you explicitly declare —
    'sudo wingman keys set anthropic --scope global' — then add
    'funded = true' to this tenant's entry in $REGISTRY_PATH and apply it
    with 'wg reload'. That is a SIGHUP, not a restart, so it interrupts
    nobody. The key itself is read on the next call and needs no reload.

    See "Operator-funded inference" in docs/SERVER.md. Tell the person that
    model-backed tools remain unavailable until you complete these steps.

NOTE
  else
    cat <<NOTE

==> '$SLUG' can make NO model call yet, so values, assessments and briefs
    will all fail for them while everything that only reads local data
    works. A tenant never inherits this box's keys by accident; somebody
    has to choose. Two ways, either is fine:

      - THEY pay: they add their own key on their Wingman page (the URL
        above), under Manage -> Keys. Nothing on the box changes.

      - YOU pay: put the key in a tier you declare —
        'sudo wingman keys set anthropic --scope global' — then add
        'funded = true' to this tenant's entry in $REGISTRY_PATH and
        apply it with 'wg reload'. That is a SIGHUP, not a restart, so
        it interrupts nobody. The key itself needs no reload at all: a
        funded tenant reads it from the file on the next call.

    See "Operator-funded inference" in docs/SERVER.md. Either way, tell
    them which — from inside their session the only symptom is a tool that
    refuses. 'wingman tenant keys' shows where each stands.

NOTE
  fi
fi
