#!/usr/bin/env bash
# Point this checkout at the repo's version-controlled hooks (#182).
#
# core.hooksPath rather than copying files into .git/hooks: a copy goes
# stale the moment a hook changes upstream, and nothing tells you — which
# is the same class of silent drift the pre-push hook itself exists to
# catch. This way the hooks that run are the ones in the tree.
#
# Local to this clone. Git deliberately does not let a repository configure
# its own hooks for whoever clones it, so this is opt-in per checkout.
#
# Usage: scripts/install-git-hooks.sh   (from anywhere in the checkout)

set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

if [ ! -d .githooks ]; then
  echo "no .githooks/ in $root — is this a current checkout?" >&2
  exit 1
fi

chmod +x .githooks/* 2>/dev/null || true
git config core.hooksPath .githooks

echo "==> core.hooksPath = .githooks"
for hook in .githooks/*; do
  [ -f "$hook" ] || continue
  echo "    $(basename "$hook")"
done
echo
echo "To undo: git config --unset core.hooksPath"
