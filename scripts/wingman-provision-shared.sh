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
#                WINGMAN_SHARED_TAILSCALE_PATHS (default "/shared"; space- or
#                  comma-separated for a service fronting more than one path),
#                WINGMAN_SHARED_TAILSCALE_PATH (deprecated single-path alias)
# OAuth (all four together, or none):
#                WINGMAN_SHARED_OAUTH_ISSUER
#                WINGMAN_SHARED_OAUTH_AUDIENCE
#                WINGMAN_SHARED_OAUTH_JWKS_URI
#                WINGMAN_SHARED_OAUTH_IDENTITIES
# OAuth browser setup (all five together, or none):
#                WINGMAN_SHARED_OAUTH_WEB_CLIENT_ID
#                WINGMAN_SHARED_OAUTH_WEB_CLIENT_SECRET
#                WINGMAN_SHARED_OAUTH_WEB_AUTHORIZE_URL
#                WINGMAN_SHARED_OAUTH_WEB_TOKEN_URL
#                WINGMAN_SHARED_OAUTH_WEB_REDIRECT_URI
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
# A shared instance may front more than one path — lobster serves both '/' and
# '/shared' from this port. Until now this script mounted exactly one, so a box
# rebuilt from it would silently not serve the others (#288).
TAILSCALE_PATHS="${WINGMAN_SHARED_TAILSCALE_PATHS:-${WINGMAN_SHARED_TAILSCALE_PATH:-/shared}}"
read -r -a TAILSCALE_PATH_LIST <<<"${TAILSCALE_PATHS//,/ }"
TAILSCALE_PATH="${TAILSCALE_PATH_LIST[0]}"   # the prefix tenant URLs are printed with
REPO_URL="git@github.com:dhk/wingman.git"
REGISTRY_PATH="/etc/wingman/tenants.toml"
OAUTH_CONFIG_PATH="${WINGMAN_SHARED_OAUTH_CONFIG:-/etc/wingman/oauth.env}"
OAUTH_ISSUER="${WINGMAN_SHARED_OAUTH_ISSUER:-}"
OAUTH_AUDIENCE="${WINGMAN_SHARED_OAUTH_AUDIENCE:-}"
OAUTH_JWKS_URI="${WINGMAN_SHARED_OAUTH_JWKS_URI:-}"
OAUTH_IDENTITIES="${WINGMAN_SHARED_OAUTH_IDENTITIES:-}"
OAUTH_WEB_CONFIG_PATH="${WINGMAN_SHARED_OAUTH_WEB_CONFIG:-/etc/wingman/oauth-web.env}"
OAUTH_WEB_CLIENT_ID="${WINGMAN_SHARED_OAUTH_WEB_CLIENT_ID:-}"
OAUTH_WEB_CLIENT_SECRET="${WINGMAN_SHARED_OAUTH_WEB_CLIENT_SECRET:-}"
OAUTH_WEB_AUTHORIZE_URL="${WINGMAN_SHARED_OAUTH_WEB_AUTHORIZE_URL:-}"
OAUTH_WEB_TOKEN_URL="${WINGMAN_SHARED_OAUTH_WEB_TOKEN_URL:-}"
OAUTH_WEB_REDIRECT_URI="${WINGMAN_SHARED_OAUTH_WEB_REDIRECT_URI:-}"
oauth_fields=0
[ -n "$OAUTH_ISSUER" ] && oauth_fields=$((oauth_fields + 1))
[ -n "$OAUTH_AUDIENCE" ] && oauth_fields=$((oauth_fields + 1))
[ -n "$OAUTH_JWKS_URI" ] && oauth_fields=$((oauth_fields + 1))
[ -n "$OAUTH_IDENTITIES" ] && oauth_fields=$((oauth_fields + 1))
if [ "$oauth_fields" -ne 0 ] && [ "$oauth_fields" -ne 4 ]; then
  echo "Shared OAuth needs all four WINGMAN_SHARED_OAUTH_* settings together." >&2
  exit 2
fi
oauth_web_fields=0
[ -n "$OAUTH_WEB_CLIENT_ID" ] && oauth_web_fields=$((oauth_web_fields + 1))
[ -n "$OAUTH_WEB_CLIENT_SECRET" ] && oauth_web_fields=$((oauth_web_fields + 1))
[ -n "$OAUTH_WEB_AUTHORIZE_URL" ] && oauth_web_fields=$((oauth_web_fields + 1))
[ -n "$OAUTH_WEB_TOKEN_URL" ] && oauth_web_fields=$((oauth_web_fields + 1))
[ -n "$OAUTH_WEB_REDIRECT_URI" ] && oauth_web_fields=$((oauth_web_fields + 1))
if [ "$oauth_web_fields" -ne 0 ] && [ "$oauth_web_fields" -ne 5 ]; then
  echo "OAuth browser setup needs all five WINGMAN_SHARED_OAUTH_WEB_* settings together." >&2
  exit 2
fi
if [ "$oauth_web_fields" -eq 5 ] && [ "$oauth_fields" -ne 4 ]; then
  echo "OAuth browser setup also requires the complete shared OAuth resource-server settings." >&2
  exit 2
fi
if [ "$oauth_web_fields" -eq 5 ]; then
  for value in "$OAUTH_WEB_CLIENT_ID" "$OAUTH_WEB_CLIENT_SECRET" "$OAUTH_WEB_AUTHORIZE_URL" "$OAUTH_WEB_TOKEN_URL" "$OAUTH_WEB_REDIRECT_URI"; do
    case "$value" in
      *[[:space:]]*) echo "OAuth browser settings must not contain whitespace." >&2; exit 2 ;;
    esac
  done
fi
if [ "$oauth_fields" -eq 4 ]; then
  for value in "$OAUTH_ISSUER" "$OAUTH_AUDIENCE" "$OAUTH_JWKS_URI" "$OAUTH_IDENTITIES"; do
    case "$value" in
      *[[:space:]]*) echo "Shared OAuth settings must not contain whitespace: $value" >&2; exit 2 ;;
    esac
  done
  case "$OAUTH_IDENTITIES" in
    /*) ;;
    *) echo "WINGMAN_SHARED_OAUTH_IDENTITIES must be an absolute path." >&2; exit 2 ;;
  esac
fi

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
OAUTH_CONFIG_CHANGED=0
OAUTH_WEB_CONFIG_CHANGED=0
if [ "$oauth_fields" -eq 4 ]; then
  OAUTH_IDENTITIES_PARENT="$(dirname "$OAUTH_IDENTITIES")"
  if [ ! -d "$OAUTH_IDENTITIES_PARENT" ]; then
    install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$OAUTH_IDENTITIES_PARENT"
  elif ! sudo -u "$SERVICE_USER" test -w "$OAUTH_IDENTITIES_PARENT" \
    || ! sudo -u "$SERVICE_USER" test -x "$OAUTH_IDENTITIES_PARENT"; then
    echo "existing OAuth identity-map parent is not writable and traversable by $SERVICE_USER: $OAUTH_IDENTITIES_PARENT" >&2
    exit 1
  fi
  if [ -e "$OAUTH_IDENTITIES" ]; then
    if [ ! -f "$OAUTH_IDENTITIES" ] \
      || ! sudo -u "$SERVICE_USER" test -r "$OAUTH_IDENTITIES" \
      || ! sudo -u "$SERVICE_USER" test -w "$OAUTH_IDENTITIES"; then
      echo "existing OAuth identity map is not a readable, writable regular file for $SERVICE_USER: $OAUTH_IDENTITIES" >&2
      exit 1
    fi
  else
    install -m 600 -o "$SERVICE_USER" -g "$SERVICE_USER" /dev/null "$OAUTH_IDENTITIES"
  fi
  OAUTH_CONFIG_TEMP="$(mktemp)"
  cat > "$OAUTH_CONFIG_TEMP" <<EOF
WINGMAN_OAUTH_ISSUER=$OAUTH_ISSUER
WINGMAN_OAUTH_AUDIENCE=$OAUTH_AUDIENCE
WINGMAN_OAUTH_JWKS_URI=$OAUTH_JWKS_URI
WINGMAN_OAUTH_IDENTITIES=$OAUTH_IDENTITIES
WINGMAN_OAUTH_ARGS=--oauth-issuer $OAUTH_ISSUER --oauth-audience $OAUTH_AUDIENCE --oauth-jwks-uri $OAUTH_JWKS_URI --oauth-identities $OAUTH_IDENTITIES
EOF
  if [ ! -f "$OAUTH_CONFIG_PATH" ] || ! cmp -s "$OAUTH_CONFIG_TEMP" "$OAUTH_CONFIG_PATH"; then
    OAUTH_CONFIG_CHANGED=1
    install -D -m 644 -o root -g wingman "$OAUTH_CONFIG_TEMP" "$OAUTH_CONFIG_PATH"
  fi
  rm -f "$OAUTH_CONFIG_TEMP"
fi
if [ "$oauth_web_fields" -eq 5 ]; then
  OAUTH_WEB_CONFIG_TEMP="$(mktemp)"
  cat > "$OAUTH_WEB_CONFIG_TEMP" <<EOF
WINGMAN_OAUTH_WEB_CLIENT_SECRET=$OAUTH_WEB_CLIENT_SECRET
WINGMAN_OAUTH_WEB_ARGS=--oauth-web-client-id $OAUTH_WEB_CLIENT_ID --oauth-web-authorize-url $OAUTH_WEB_AUTHORIZE_URL --oauth-web-token-url $OAUTH_WEB_TOKEN_URL --oauth-web-redirect-uri $OAUTH_WEB_REDIRECT_URI
EOF
  if [ ! -f "$OAUTH_WEB_CONFIG_PATH" ] || ! cmp -s "$OAUTH_WEB_CONFIG_TEMP" "$OAUTH_WEB_CONFIG_PATH"; then
    OAUTH_WEB_CONFIG_CHANGED=1
    install -D -m 600 -o "$SERVICE_USER" -g "$SERVICE_USER" "$OAUTH_WEB_CONFIG_TEMP" "$OAUTH_WEB_CONFIG_PATH"
  fi
  rm -f "$OAUTH_WEB_CONFIG_TEMP"
fi

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
UNIT_PATH="$UNIT_DIR/wingman-mcp.service"
UNIT_TEMP="$(mktemp)"
cat > "$UNIT_TEMP" <<EOF
[Unit]
Description=Wingman shared multi-tenant MCP server (RFC-048)
After=network.target

[Service]
EnvironmentFile=-%h/.config/wingman/wingman.env
EnvironmentFile=-%h/.config/wingman/secrets.env
EnvironmentFile=-$OAUTH_CONFIG_PATH
EnvironmentFile=-$OAUTH_WEB_CONFIG_PATH
ExecStart=%h/.local/bin/wingman-mcp --http --port $PORT --tenant-registry $REGISTRY_PATH \$WINGMAN_OAUTH_ARGS \$WINGMAN_OAUTH_WEB_ARGS
Restart=on-failure
RestartSec=2
# A slow port release must never become a PERMANENT outage (#415).
#
# systemd's default StartLimitBurst is 5: five failed starts inside the
# interval and it gives up, leaving the unit dead until somebody notices.
# On this box a redeploy produced 24 consecutive bind failures over ~50
# seconds — the outgoing process was still draining sessions and holding
# :$PORT — and every one of them counted. It came up only because the
# limit was not enforced over that window.
#
# The real fix is that redeploy-shared now waits for the port instead of
# racing it. This is the backstop for every other path: a manual
# 'systemctl restart', a reboot, an OOM kill. Giving up on a service that
# serves every tenant is a worse failure than retrying it for a while.
StartLimitBurst=0

[Install]
WantedBy=default.target
EOF
UNIT_CHANGED=0
if [ ! -f "$UNIT_PATH" ] || ! cmp -s "$UNIT_TEMP" "$UNIT_PATH"; then
  UNIT_CHANGED=1
  install -m 644 -o "$SERVICE_USER" -g "$SERVICE_USER" "$UNIT_TEMP" "$UNIT_PATH"
fi
rm -f "$UNIT_TEMP"
# 'systemctl --user' needs XDG_RUNTIME_DIR pointed at this account's own
# runtime dir to reach its session bus at all when invoked via sudo from
# root — 'sudo -iu' alone isn't enough (this is the exact fix
# 'infrastructure/upgrade_all.py' already needed, RFC-042); without it
# this fails with "Failed to connect to bus: No medium found".
sudo -u "$SERVICE_USER" env "XDG_RUNTIME_DIR=/run/user/$SERVICE_UID" \
  systemctl --user daemon-reload
SERVICE_WAS_ACTIVE=0
if sudo -u "$SERVICE_USER" env "XDG_RUNTIME_DIR=/run/user/$SERVICE_UID" \
  systemctl --user is-active --quiet wingman-mcp.service; then
  SERVICE_WAS_ACTIVE=1
fi
sudo -u "$SERVICE_USER" env "XDG_RUNTIME_DIR=/run/user/$SERVICE_UID" \
  systemctl --user enable --now wingman-mcp.service
if [ "$SERVICE_WAS_ACTIVE" -eq 1 ] && { [ "$OAUTH_CONFIG_CHANGED" -eq 1 ] || [ "$OAUTH_WEB_CONFIG_CHANGED" -eq 1 ] || [ "$UNIT_CHANGED" -eq 1 ]; }; then
  say "  service configuration changed — restarting the shared service"
  sudo -u "$SERVICE_USER" env "XDG_RUNTIME_DIR=/run/user/$SERVICE_UID" \
    systemctl --user restart wingman-mcp.service
fi

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
  for mount_path in "${TAILSCALE_PATH_LIST[@]}"; do
    say "  mounting $mount_path"
    tailscale funnel --bg --set-path "$mount_path" "http://127.0.0.1:$PORT"
  done
else
  say "tailscale not found — skipping. Run manually later:"
  for mount_path in "${TAILSCALE_PATH_LIST[@]}"; do
    say "  tailscale funnel --bg --set-path $mount_path http://127.0.0.1:$PORT"
  done
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
