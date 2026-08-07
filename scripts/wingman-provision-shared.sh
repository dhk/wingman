#!/usr/bin/env bash
# wingman-provision-shared.sh — stand up the RFC-048 shared multi-tenant
# wingman-mcp process on a fresh Ubuntu/Debian box (root-run, once per box).
#
# Idempotent by design: every step checks its own precondition before
# acting, so re-running after a partial failure (or against a box that's
# already partway through) only does whatever is still missing — the
# exact gap a hand-run version of this checklist kept tripping on
# (missing 'wingman' group, HTTPS clone URLs prompting for GitHub
# credentials on a fresh service account with no git identity, '~'
# silently expanding in the CALLING shell instead of the target
# account's, PATH not surviving a non-interactive 'bash -c', and
# 'systemctl --user' unable to reach a fresh account's session bus at
# all without XDG_RUNTIME_DIR pointed at it explicitly — the same fix
# 'infrastructure/upgrade_all.py' already needed for this exact reason,
# RFC-042).
#
# Usage: sudo ./wingman-provision-shared.sh
# Env overrides: WINGMAN_SHARED_USER (default wingman-shared),
#                WINGMAN_SHARED_PORT (default 8789),
#                WINGMAN_SHARED_TAILSCALE_PATH (default /shared)
#
# What this does NOT do: create per-tenant workspaces or tokens — run
# 'wingman-add-tenant.sh <slug>' once per tenant afterward. Nor does it
# populate this account's own ~/.config/wingman/{wingman.env,secrets.env}
# (RFC-046) — tenant Anthropic/Voyage keys are resolved strictly from
# each TENANT's own workspace file (RFC-048's strict_provider_keys), so
# this account needs no API keys of its own for the core flow to work.

set -euo pipefail

SERVICE_USER="${WINGMAN_SHARED_USER:-wingman-shared}"
PORT="${WINGMAN_SHARED_PORT:-8789}"
TAILSCALE_PATH="${WINGMAN_SHARED_TAILSCALE_PATH:-/shared}"
REPO_URL="git@github.com:dhk/wingman.git"
REGISTRY_PATH="/etc/wingman/tenants.toml"

say() { printf '==> %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

say "1/7 wingman group"
getent group wingman >/dev/null || groupadd wingman

say "2/7 service account: $SERVICE_USER"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$SERVICE_USER"
fi
usermod -aG wingman "$SERVICE_USER"
loginctl enable-linger "$SERVICE_USER"
SERVICE_UID="$(id -u "$SERVICE_USER")"
# Force the user manager (and its /run/user/<uid> runtime dir) up now,
# rather than hoping linger's own timing wins a race against step 6's
# 'systemctl --user' calls below — a fresh account has no login session
# to have started it yet.
systemctl start "user@${SERVICE_UID}.service" 2>/dev/null || true

say "3/7 registry directory + file (root-owned, group-readable, no secrets in it)"
install -d -m 750 -o root -g wingman /etc/wingman
[ -f "$REGISTRY_PATH" ] || install -m 644 /dev/null "$REGISTRY_PATH"

say "4/7 SSH deploy key — this account has no GitHub identity of its own"
SSH_DIR="/home/$SERVICE_USER/.ssh"
KEY_PATH="$SSH_DIR/id_ed25519"
if [ ! -f "$KEY_PATH" ]; then
  sudo -iu "$SERVICE_USER" bash -c "ssh-keygen -t ed25519 -C '$SERVICE_USER@$(hostname)' -f ~/.ssh/id_ed25519 -N ''"
  echo
  say "New deploy key — add this as a READ-ONLY deploy key on dhk/wingman"
  say "(GitHub -> repo -> Settings -> Deploy keys -> Add deploy key), then press enter:"
  cat "$KEY_PATH.pub"
  read -r _
fi

say "5/7 clone + install wingman (SSH URL — this box's git identity is deploy-key-only, HTTPS prompts for credentials that don't exist)"
# ~/src/wingman, not ~/wingman: infrastructure/upgrade_all.py's
# REPO_SUBPATH assumes this exact convention (CLAUDE.md's documented
# code-location default, every account) for the cross-account
# wingman-upgrade-all sweep to find this checkout without an explicit
# WINGMAN_UPGRADE_SOURCE_<user> override.
if [ ! -d "/home/$SERVICE_USER/src/wingman/.git" ]; then
  sudo -iu "$SERVICE_USER" bash -c "mkdir -p ~/src && git clone $REPO_URL ~/src/wingman"
fi
sudo -iu "$SERVICE_USER" bash -c "~/src/wingman/scripts/wingman-tool-install.sh ~/src/wingman"

say "6/7 systemd unit"
UNIT_DIR="/home/$SERVICE_USER/.config/systemd/user"
sudo -iu "$SERVICE_USER" mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/wingman-mcp.service" <<EOF
[Unit]
Description=Wingman shared multi-tenant MCP server (RFC-048)
After=network.target

[Service]
EnvironmentFile=-%h/.config/wingman/wingman.env
EnvironmentFile=-%h/.config/wingman/secrets.env
ExecStart=%h/.local/bin/wingman-mcp --http --port $PORT --tenant-registry $REGISTRY_PATH
Restart=on-failure

[Install]
WantedBy=default.target
EOF
chown "$SERVICE_USER:$SERVICE_USER" "$UNIT_DIR/wingman-mcp.service"
# 'systemctl --user' needs XDG_RUNTIME_DIR pointed at this account's own
# runtime dir to reach its session bus at all when invoked via sudo from
# root — 'sudo -iu' alone isn't enough (this is the exact fix
# 'infrastructure/upgrade_all.py' already needed, RFC-042); without it
# this fails with "Failed to connect to bus: No medium found".
sudo -u "$SERVICE_USER" env "XDG_RUNTIME_DIR=/run/user/$SERVICE_UID" \
  systemctl --user daemon-reload
sudo -u "$SERVICE_USER" env "XDG_RUNTIME_DIR=/run/user/$SERVICE_UID" \
  systemctl --user enable --now wingman-mcp.service

say "7/8 tailscale mount (stripping proxy — the shared process itself runs with no --prefix)"
# 'funnel', never plain 'serve': funnel is a per-HOSTNAME toggle, not a
# per-path one — a bare 'tailscale serve --set-path ...' silently drops
# Funnel for the WHOLE hostname (every other mounted path too, not just
# this one), demoting dhk/trent/alexandria's already-public instances
# back to tailnet-only as a side effect. Hit live twice on the same box:
# once by hand, then again because this exact line still said 'serve'
# when this script was first written, and a later re-run of the
# (idempotent) provisioning script replayed the same mistake against
# production.
if command -v tailscale >/dev/null 2>&1; then
  tailscale funnel --bg --set-path "$TAILSCALE_PATH" "http://127.0.0.1:$PORT"
else
  say "tailscale not found — skipping. Run manually later:"
  say "  tailscale funnel --bg --set-path $TAILSCALE_PATH http://127.0.0.1:$PORT"
fi

say "8/8 host service registry"
# Runs AFTER the funnel mount, because it reads the funnel to learn which
# paths to claim rather than assuming them (#288). Skips itself cleanly when
# the registry helper isn't on this box.
"$(dirname "$0")/wingman-register-service.sh"

echo
say "Done. Add tenants with: sudo ./wingman-add-tenant.sh <slug>"
say "Check status with:      sudo -iu $SERVICE_USER bash -c 'export PATH=\"\$HOME/.local/bin:\$PATH\"; wingman tenant url <slug> --port $PORT --tunnel-prefix $TAILSCALE_PATH'"
say "                        ('wingman' directly, not the 'wg' alias — that only"
say "                        exists in an interactive shell that's sourced its own"
say "                        .bashrc, which $SERVICE_USER was never given)"
say "One more step, by hand: add '$SERVICE_USER' to WINGMAN_UPGRADE_USERS in"
say "root's wingman-upgrade-all.service (docs/SERVER.md §7/§9), so this account's"
say "checkout gets swept into the same nightly automated upgrade as everyone else."
