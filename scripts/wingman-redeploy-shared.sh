#!/usr/bin/env bash
# wingman-redeploy-shared.sh — pull, reinstall, restart, and verify the
# shared multi-tenant process in one tested, idempotent, reviewable
# script (#150's "safe restart primitive" — the narrower first step,
# deliberately NOT wired to any automatic CI/CD trigger yet: every
# tenant on the shared process gets interrupted by a restart, and this
# repo merges often enough that auto-deploy-on-every-merge would mean
# surprise, unattended interruptions for people who aren't the one
# deploying — a decision worth making explicitly later, not defaulted
# into now).
#
# Exists to replace exactly the failure mode #150 named: an agent (or a
# human) hand-constructing SSH commands live, including the systemctl
# --user / XDG_RUNTIME_DIR dance this repo has already gotten wrong
# twice in one night (see docs/SERVER.md §9's pitfalls list). One
# command, run identically every time, with a real health check
# afterward instead of "looked fine in the terminal."
#
# Usage: sudo ./wingman-redeploy-shared.sh
# Env overrides: WINGMAN_SHARED_USER (default wingman-shared),
#                WINGMAN_SHARED_PORT (default 8789)

set -euo pipefail

SERVICE_USER="${WINGMAN_SHARED_USER:-wingman-shared}"
PORT="${WINGMAN_SHARED_PORT:-8789}"

say() { printf '==> %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "no such account: $SERVICE_USER — run wingman-provision-shared.sh first" >&2
  exit 1
fi

WS_UID="$(id -u "$SERVICE_USER")"

# systemctl --user needs XDG_RUNTIME_DIR pointed at this account's own
# runtime dir to reach its session bus at all when invoked via sudo from
# root — 'sudo -iu' alone isn't enough (RFC-042; hit live twice tonight,
# see docs/SERVER.md §9/§5).
sudo_user_ctl() {
  sudo -u "$SERVICE_USER" env "XDG_RUNTIME_DIR=/run/user/$WS_UID" systemctl --user "$@"
}

say "1/4 pulling latest code (~/src/wingman)"
# One line, ';'-joined — never a multi-line 'bash -c' payload under
# 'sudo -i'. This is a hard constraint of sudo, not a style preference,
# and not the flakiness it first looks like: 'sudo -i'/'-s' do not exec
# argv directly. They join argv with spaces and backslash-escape every
# character outside [A-Za-z0-9_-$], then run '<login shell> -c "<one big
# string>"' (sudo's parse_args.c). An embedded newline therefore arrives
# as backslash-newline — a line continuation — which the login shell
# deletes outright, collapsing both lines into a single command.
#
# So the second line's words become extra arguments to the first line's
# command, 100% deterministically. 'export PATH=... cd ~/src/wingman &&
# git pull' fails with "not a valid identifier" naming the EXPANDED path
# ('cd' is itself a valid name, so it's silently exported; the path
# isn't), and '&&' then short-circuits so the pull never runs. The worse
# case is when every stray word happens to be a valid identifier:
# 'export' absorbs them all, exit 0, no output, a silent no-op.
#
# Also note sudo deliberately does NOT escape '$', so the '$HOME'/'$PATH'
# below are expanded by the target account's login shell rather than by
# this inner bash — the same values here, so harmless, but the single
# quotes are not protecting what they appear to protect.
sudo -iu "$SERVICE_USER" bash -c 'export PATH="$HOME/.local/bin:$PATH"; cd ~/src/wingman && git pull --ff-only'

say "2/4 reinstalling"
sudo -iu "$SERVICE_USER" bash -c 'export PATH="$HOME/.local/bin:$PATH"; cd ~/src/wingman && uv tool install --reinstall .'

say "3/4 restarting wingman-mcp.service"
BEFORE_STARTED_AT=$(curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -o '"started_at":"[^"]*"' || true)
sudo_user_ctl restart wingman-mcp.service

say "4/4 verifying health"
DEADLINE=$((SECONDS + 30))
STATUS=""
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  STATUS=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" 2>/dev/null || echo "000")
  [ "$STATUS" = "200" ] && break
  sleep 1
done

if [ "$STATUS" != "200" ]; then
  echo "ERROR: /health did not return 200 within 30s (last: $STATUS). Not confirmed healthy." >&2
  echo "Check: sudo -u $SERVICE_USER env XDG_RUNTIME_DIR=/run/user/$WS_UID systemctl --user status wingman-mcp.service" >&2
  echo "Logs:  sudo -u $SERVICE_USER env XDG_RUNTIME_DIR=/run/user/$WS_UID journalctl --user -u wingman-mcp.service -n 50 --no-pager" >&2
  exit 1
fi

AFTER_STARTED_AT=$(curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -o '"started_at":"[^"]*"' || true)
if [ -n "$BEFORE_STARTED_AT" ] && [ "$BEFORE_STARTED_AT" = "$AFTER_STARTED_AT" ]; then
  echo "ERROR: /health returned 200 but started_at didn't change — the restart may not have taken effect (old process still serving?)." >&2
  exit 1
fi

say "Healthy. $AFTER_STARTED_AT"
say "Every tenant on this process was briefly interrupted by the restart — expected, not a bug."
