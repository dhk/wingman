#!/usr/bin/env bash
# wingman-install-register-helper.sh — opt in to approving people from chat.
#
# Installs scripts/wingman-register-tenant.py as a root-owned program and ONE
# sudoers rule letting the shared service's account run it (RFC-086, #658). The
# tenant registry stays root-owned; the helper can only append an unprivileged,
# unfunded tenant whose workspace the service account has already created.
#
# Usage: sudo ./wingman-install-register-helper.sh \
#          --service-user wingman-shared --data-root /home/wingman-shared/tenants \
#          [--registry /etc/wingman/tenants.toml] [--dry-run]
#
# Re-running is safe: files are replaced with identical content. To uninstall:
# remove /etc/sudoers.d/wingman-register-tenant first, then the other two paths.
#
# The helper is copied FROM THIS CHECKOUT at install time. Run this from a
# checkout you trust (not one the service account can write), because the copy
# is what root will execute.

set -euo pipefail

HELPER_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/wingman-register-tenant.py"
HELPER_DIR="/usr/local/libexec/wingman"
HELPER_DEST="$HELPER_DIR/register-tenant"
CONF_DEST="/etc/wingman/register-helper.conf"
SUDOERS_DEST="/etc/sudoers.d/wingman-register-tenant"

SERVICE_USER=""
DATA_ROOT=""
REGISTRY="${WINGMAN_SHARED_REGISTRY:-/etc/wingman/tenants.toml}"
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --service-user) SERVICE_USER="${2:?--service-user needs a name}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:?--data-root needs a path}"; shift 2 ;;
    --registry) REGISTRY="${2:?--registry needs a path}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$SERVICE_USER" ] && [ -n "$DATA_ROOT" ] || {
  echo "usage: $0 --service-user USER --data-root DIR [--registry FILE] [--dry-run]" >&2
  exit 2
}
# These land in a root-owned config and a sudoers file: refuse anything that is
# not plainly a name or an absolute path, rather than trying to escape it.
case "$SERVICE_USER" in
  ''|*[!a-z0-9_-]*) echo "service user must be a plain account name: $SERVICE_USER" >&2; exit 2 ;;
esac
for path in "$DATA_ROOT" "$REGISTRY"; do
  case "$path" in
    /*) ;;
    *) echo "paths must be absolute: $path" >&2; exit 2 ;;
  esac
  case "$path" in
    *[!A-Za-z0-9/._-]*) echo "path has characters this installer will not write to root files: $path" >&2; exit 2 ;;
  esac
done

SUDOERS_LINE="$SERVICE_USER ALL=(root) NOPASSWD: $HELPER_DEST"
CONF_BODY="REGISTRY=$REGISTRY
DATA_ROOT=$DATA_ROOT
SERVICE_USER=$SERVICE_USER"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "would install  $HELPER_SRC -> $HELPER_DEST (root:root 0755)"
  echo "would write    $CONF_DEST (root:root 0644):"
  printf '%s\n' "$CONF_BODY" | sed 's/^/    /'
  echo "would write    $SUDOERS_DEST (root:root 0440), after visudo -c accepts it:"
  echo "    $SUDOERS_LINE"
  exit 0
fi

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0 ..." >&2; exit 1; }
[ -f "$HELPER_SRC" ] || { echo "missing $HELPER_SRC" >&2; exit 1; }
id "$SERVICE_USER" >/dev/null 2>&1 || { echo "no such account: $SERVICE_USER" >&2; exit 1; }
[ -d "$DATA_ROOT" ] || { echo "no such directory: $DATA_ROOT" >&2; exit 1; }
[ -f "$REGISTRY" ] || { echo "no registry at $REGISTRY - run wingman-provision-shared.sh first" >&2; exit 1; }
command -v visudo >/dev/null 2>&1 || { echo "visudo is required to check the sudoers rule" >&2; exit 1; }

say() { printf '==> %s\n' "$*"; }

# A root-owned program in a root-owned, non-writable directory chain.
say "installing the helper"
install -d -o root -g root -m 0755 "$HELPER_DIR"
install -o root -g root -m 0755 "$HELPER_SRC" "$HELPER_DEST"

say "writing the helper's config"
install -d -o root -g root -m 0755 "$(dirname "$CONF_DEST")" 2>/dev/null || true
tmp_conf="$(mktemp)"
printf '%s\n' "$CONF_BODY" > "$tmp_conf"
install -o root -g root -m 0644 "$tmp_conf" "$CONF_DEST"
rm -f "$tmp_conf"

# Check the rule BEFORE it is live: a broken file in sudoers.d can lock sudo.
say "checking the sudoers rule"
tmp_rule="$(mktemp)"
printf '%s\n' "$SUDOERS_LINE" > "$tmp_rule"
if ! visudo -cf "$tmp_rule" >/dev/null; then
  rm -f "$tmp_rule"
  echo "visudo rejected the rule; nothing was enabled (the helper files are in place but unreachable)." >&2
  exit 1
fi
install -o root -g root -m 0440 "$tmp_rule" "$SUDOERS_DEST"
rm -f "$tmp_rule"

say "done. '$SERVICE_USER' may now run $HELPER_DEST as root, and nothing else."
say "Check: sudo -u $SERVICE_USER sudo -n $HELPER_DEST nosuchslug   (expect: refused: ...)"
