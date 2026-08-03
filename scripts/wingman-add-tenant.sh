#!/usr/bin/env bash
# wingman-add-tenant.sh <slug> — provision one tenant's workspace under
# the shared multi-tenant process (RFC-048) and register them, issuing
# their first connector URL. Run after wingman-provision-shared.sh.
#
# Idempotent: skips workspace init if the tenant's data_dir already has
# a wingman.db (a workspace created by hand, e.g. via 'wingman init',
# before this script existed), and refuses if the slug is already in
# the registry rather than duplicating the entry.

set -euo pipefail

SERVICE_USER="${WINGMAN_SHARED_USER:-wingman-shared}"
PORT="${WINGMAN_SHARED_PORT:-8789}"
REGISTRY_PATH="/etc/wingman/tenants.toml"
SLUG="${1:?usage: $0 <slug>}"

say() { printf '==> %s\n' "$*"; }

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

if [ -f "$DATA_DIR/wingman.db" ]; then
  say "workspace for '$SLUG' already exists at $DATA_DIR — skipping init"
else
  say "initializing workspace for '$SLUG'"
  sudo -iu "$SERVICE_USER" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; WINGMAN_DATA_DIR=$DATA_DIR wingman init"
fi

say "registering '$SLUG' in $REGISTRY_PATH"
cat >> "$REGISTRY_PATH" <<EOF

[[tenant]]
slug = "$SLUG"
data_dir = "$DATA_DIR"
EOF

say "issuing first token and reloading the running server"
sudo -iu "$SERVICE_USER" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; wingman tenant rotate-token $SLUG --port $PORT"
