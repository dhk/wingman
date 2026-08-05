#!/usr/bin/env bash
# wingman-migrate-tenant.sh <slug> — RFC-048 Phase 3: migrate ONE
# existing shape-B account's live workspace onto the shared
# multi-tenant process, via 'wingman backup'/'wingman restore'
# unmodified, per the RFC's own specified procedure.
#
# Run as root on lobster, once per account:
#   sudo ./wingman-migrate-tenant.sh dhk
#   sudo ./wingman-migrate-tenant.sh trent
#
# Dry-run by default — prints every step without changing anything.
# Pass --apply to actually do it.
#
# Assumes: <slug> is both the existing Unix account name AND the tenant
# slug to register it under (matches jason/bob's convention). The
# account's own checkout/workspace already exists per docs/SERVER.md's
# shape-B layout (~/.config/wingman/{wingman.env,secrets.env},
# XDG-default data dir).
#
# What this does NOT do (by design, matching RFC-048's rollout plan):
#   - Does not stop or disable the account's existing wingman-mcp.service.
#     Both the old shape-B process and the new shared-process tenant can
#     read/run against the SAME restored data independently — but once
#     you cut over, DON'T run both against live traffic at once, since
#     'wingman restore' is a one-time snapshot, not an ongoing sync. Old
#     unit stays enabled until you've verified the new tenant works, then
#     disable (not delete) it by hand — see the printed reminder at the end.
#
# Leaves a durable record of what happened and when, at
# $DATA_DIR/.migrated-from-shape-b — a real incident on 2026-08-04 found
# this exact migration had been run and then quietly abandoned mid-way
# (interrupted by unrelated live issues) with no trace anywhere: the
# tenant sat registered and populated for 8+ hours before anyone
# reading the registry could tell it wasn't just an ordinary, current,
# already-cut-over tenant. This file is that trace.
set -euo pipefail

SLUG="${1:?usage: sudo $0 <slug> [--apply]}"
APPLY="${2:-}"
DRY_RUN=1
[ "$APPLY" = "--apply" ] && DRY_RUN=0

SERVICE_USER="${WINGMAN_SHARED_USER:-wingman-shared}"
PORT="${WINGMAN_SHARED_PORT:-8789}"
TAILSCALE_PATH="${WINGMAN_SHARED_TAILSCALE_PATH:-/shared}"
STAGE="/var/tmp/wingman-migration/$SLUG"
DATA_DIR="/home/$SERVICE_USER/tenants/$SLUG"
ADD_TENANT_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/wingman-add-tenant.sh"

say() { printf '==> %s\n' "$*"; }
# Dry-run wrapper: takes the command as SEPARATE ARGUMENTS, never a
# pre-flattened string — no eval, so quoting only ever has to be correct
# once (bash's own argument passing), not threaded through a second
# round of string re-parsing. '%q' in the dry-run print shows exactly
# what would run, shell-quoted, not an approximation.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
  else
    printf '[apply]  '
    printf ' %q' "$@"
    printf '\n'
    "$@"
  fi
}

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0 $SLUG ${APPLY}" >&2
  exit 1
fi
if ! id "$SLUG" >/dev/null 2>&1; then
  echo "no Unix account '$SLUG' on this box" >&2
  exit 1
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "no '$SERVICE_USER' account — run wingman-provision-shared.sh first" >&2
  exit 1
fi
if [ ! -x "$ADD_TENANT_SCRIPT" ]; then
  echo "expected wingman-add-tenant.sh next to this script at $ADD_TENANT_SCRIPT" >&2
  exit 1
fi
if grep -q "slug = \"$SLUG\"" /etc/wingman/tenants.toml 2>/dev/null; then
  echo "tenant '$SLUG' is already registered — nothing to migrate." >&2
  exit 1
fi
if [ -d "$DATA_DIR" ]; then
  echo "$DATA_DIR already exists — refusing to overwrite. Clean it up by hand first if this is a re-run." >&2
  exit 1
fi

echo "=== Phase 3 migration: $SLUG -> shared process tenant ($DRY_RUN=dry-run? $DRY_RUN) ==="

say "1/6 backing up $SLUG's live shape-B workspace"
run mkdir -p "$STAGE"
run chown "$SLUG:$SLUG" "$STAGE"
run sudo -iu "$SLUG" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; wingman backup $STAGE"

if [ "$DRY_RUN" -eq 0 ]; then
  ARCHIVE=$(ls -t "$STAGE"/wingman-backup-*.tar.gz 2>/dev/null | head -1)
  if [ -z "$ARCHIVE" ]; then
    echo "no backup archive found in $STAGE after 'wingman backup' — aborting." >&2
    exit 1
  fi
  say "found backup: $ARCHIVE"
else
  ARCHIVE="$STAGE/wingman-backup-<timestamp>.tar.gz"
fi

say "2/6 restoring into the new tenant data dir ($DATA_DIR)"
# $STAGE itself, not just the archive, needs to be traversable by
# $SERVICE_USER — root's own umask when creating it is not guaranteed to
# leave it world-readable, and $SERVICE_USER (not root) is who actually
# opens the file two lines down (via sudo -iu, i.e. running AS that
# account, which does not bypass filesystem permission checks the way
# root reading it directly would).
run chmod 755 "$STAGE"
run chmod 644 "$ARCHIVE"
run sudo -iu "$SERVICE_USER" bash -c "export PATH=\"\$HOME/.local/bin:\$PATH\"; WINGMAN_DATA_DIR=$DATA_DIR wingman restore $ARCHIVE"

say "3/6 carrying over $SLUG's provider + GitHub-issues keys into the tenant's own keys.env"
say "    (tenants never inherit the host/global secrets tiers by design — RFC-048 keeps this strict)"
SECRETS_FILE="/home/$SLUG/.config/wingman/secrets.env"
if [ "$DRY_RUN" -eq 0 ]; then
  if [ ! -f "$SECRETS_FILE" ]; then
    echo "no $SECRETS_FILE found — $SLUG hasn't done the RFC-046 split yet? Aborting." >&2
    exit 1
  fi
  ANTHROPIC_KEY=$(grep '^ANTHROPIC_API_KEY=' "$SECRETS_FILE" | tail -1 | cut -d= -f2-)
  VOYAGE_KEY=$(grep '^VOYAGE_API_KEY=' "$SECRETS_FILE" | tail -1 | cut -d= -f2-)
  # GITHUB_API_ISSUES_KEY: carried over for real now — Tenant.config() and
  # application.feature_request's default runner both read it from this
  # exact file (infrastructure/tenants.py, application/feature_request.py).
  # Previously copied nowhere on purpose, since nothing read it from here;
  # that's fixed, so this line now actually does something.
  GITHUB_KEY=$(grep '^GITHUB_API_ISSUES_KEY=' "$SECRETS_FILE" | tail -1 | cut -d= -f2-)
  KEYS_ENV="$DATA_DIR/keys.env"
  {
    [ -n "$ANTHROPIC_KEY" ] && echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY"
    [ -n "$VOYAGE_KEY" ] && echo "VOYAGE_API_KEY=$VOYAGE_KEY"
    [ -n "$GITHUB_KEY" ] && echo "GITHUB_API_ISSUES_KEY=$GITHUB_KEY"
  } > "$KEYS_ENV"
  chown "$SERVICE_USER:$SERVICE_USER" "$KEYS_ENV"
  chmod 600 "$KEYS_ENV"
  [ -s "$KEYS_ENV" ] || echo "WARNING: no anthropic/voyage/github keys found in $SECRETS_FILE — $KEYS_ENV is empty." >&2
else
  echo "[dry-run] would read ANTHROPIC_API_KEY/VOYAGE_API_KEY/GITHUB_API_ISSUES_KEY from $SECRETS_FILE and write $DATA_DIR/keys.env (mode 600)"
fi

say "4/6 registering '$SLUG' in the tenant registry and issuing its first token"
run "$ADD_TENANT_SCRIPT" "$SLUG"

say "5/6 recording that this migration happened (see this script's own header)"
if [ "$DRY_RUN" -eq 0 ]; then
  {
    echo "migrated_from_account=$SLUG"
    echo "migrated_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "source_backup=$ARCHIVE"
    echo "cutover_confirmed=false  # flip by hand once you've verified the new tenant and disabled the old unit"
  } > "$DATA_DIR/.migrated-from-shape-b"
  chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/.migrated-from-shape-b"
else
  echo "[dry-run] would write $DATA_DIR/.migrated-from-shape-b (slug, timestamp, source archive, cutover_confirmed=false)"
fi

say "6/6 cleaning up the staged backup"
run rm -rf "$STAGE"

echo
echo "=== done ($([ $DRY_RUN -eq 1 ] && echo 'dry-run only, nothing changed' || echo 'applied')) ==="
echo
echo "Next steps (manual, on purpose):"
echo "  1. sudo -iu $SERVICE_USER bash -c 'export PATH=\"\$HOME/.local/bin:\$PATH\"; \\"
echo "       wingman tenant url $SLUG --port $PORT --tunnel-prefix $TAILSCALE_PATH'"
echo "     -> gives you the new connector URL + token for $SLUG. Calls 'wingman'"
echo "     directly, not the 'wg' alias — that alias only exists in an"
echo "     interactive shell that's sourced its own .bashrc (typically a"
echo "     human's own account, per docs/SERVER.md §1's setup step), which"
echo "     '\$SERVICE_USER' was never given and this non-interactive"
echo "     'bash -c' wouldn't source anyway even if it had been."
echo "  2. Point $SLUG's Claude connector at that URL, verify it actually"
echo "     works (e.g. list personas, run a real tool call) before touching"
echo "     the old service."
echo "  3. Once verified live: edit $DATA_DIR/.migrated-from-shape-b, set"
echo "     cutover_confirmed=true — the one durable signal that this tenant"
echo "     is the real, current instance, not an unfinished migration."
echo "  4. Only then: disable (NOT delete) the old per-account units,"
echo "     keeping them as rollback for at least one full operating cycle"
echo "     per RFC-048's own rollout plan:"
echo "       sudo -iu $SLUG env XDG_RUNTIME_DIR=/run/user/\$(id -u $SLUG) \\"
echo "         systemctl --user disable --now wingman-mcp.service wingman-overnight.timer"
echo "  5. Do NOT run both the old shape-B process and the new tenant"
echo "     against live traffic at the same time after step 4 — the restore"
echo "     was a one-time snapshot, not an ongoing sync; they will diverge."
