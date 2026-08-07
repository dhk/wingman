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
REGISTRY_PATH="/etc/wingman/tenants.toml"
SLUG="${1:?usage: $0 <slug> [--telemetry|--no-telemetry]}"
TELEMETRY="${2:-}"
case "$TELEMETRY" in
  --telemetry|--no-telemetry|"") ;;
  *) echo "unknown option: $TELEMETRY (use --telemetry or --no-telemetry)" >&2; exit 2 ;;
esac

say() { printf '==> %s\n' "$*"; }

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

say "registering '$SLUG' in $REGISTRY_PATH"
cat >> "$REGISTRY_PATH" <<EOF

[[tenant]]
slug = "$SLUG"
data_dir = "$DATA_DIR"
EOF

say "issuing first token and reloading the running server"
# --tunnel-prefix matches this same script's tailscale mount (see
# wingman-provision-shared.sh's '--set-path'): the printed tunnel URL
# needs it even though the shared process's own local bind never does.
sudo -iu "$SERVICE_USER" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; wingman tenant rotate-token $SLUG --port $PORT --tunnel-prefix $TAILSCALE_PATH"
