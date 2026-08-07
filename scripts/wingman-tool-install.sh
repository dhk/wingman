#!/usr/bin/env bash
# wingman-tool-install.sh — install a checkout as a uv tool, pinned to the
# versions its own uv.lock records (#302).
#
# 'uv tool install' resolves dependencies fresh and ignores uv.lock, so every
# install path here produced a running service using versions CI, 'uv run
# --frozen', and the lockfile had never agreed to. Measured on lobster: mcp
# 1.29.0 installed against a lock pinning 1.28.1, and pydantic_settings 2.15.0
# against 2.14.2. mcp is the protocol this product speaks; nothing reported the
# gap, and nothing would have.
#
# Exists as one script rather than three copies because there are three install
# paths — 'wg upgrade', the shared redeploy, and shared provisioning — and a
# fix applied to two of three is the same defect with a smaller blast radius.
#
# Degrades rather than blocks: a checkout with no lockfile, or an export that
# fails, still installs and says plainly that its dependencies resolved freely.
# An install that resolves freely beats no install; an install that resolves
# freely and pretends otherwise is what this fixes.
#
# Usage: wingman-tool-install.sh [checkout]   (default: current directory)

set -euo pipefail

REPO="${1:-$PWD}"
cd "$REPO"

say() { printf '==> %s\n' "$*"; }

CONSTRAINTS=""
# Explicit 'return 0': as an EXIT trap under 'set -e', a cleanup whose last
# command is a false test makes the whole script exit non-zero — an install
# that worked, reported as a failure.
cleanup() { if [ -n "$CONSTRAINTS" ]; then rm -f "$CONSTRAINTS"; fi; return 0; }
trap cleanup EXIT

if [ -f uv.lock ]; then
  candidate="$(mktemp)"
  if uv export --frozen --no-dev --no-emit-project --format requirements-txt \
      >"$candidate" 2>/dev/null && [ -s "$candidate" ]; then
    CONSTRAINTS="$candidate"
  else
    rm -f "$candidate"
    say "uv.lock is present but could not be exported — installing unpinned."
  fi
else
  say "no uv.lock in $REPO — installing unpinned."
fi

if [ -n "$CONSTRAINTS" ]; then
  uv tool install --reinstall --constraints "$CONSTRAINTS" .
else
  say "dependencies resolved freely: installed versions may differ from tested ones."
  uv tool install --reinstall .
fi
