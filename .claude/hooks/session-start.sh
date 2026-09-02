#!/bin/bash
# Make a Claude Code on the web checkout usable: full git history first,
# then dependencies.
#
# The container clones this repo shallow. Three things break, and none of
# them announce themselves as a clone-depth problem:
#
#   1. 'origin/main' is stale and 'git fetch' does not fix it, so a session
#      reads a months-old main, concludes work is unstarted, and rebuilds
#      what someone already shipped. That cost a full duplicate
#      implementation of #506 in one session, and the CI comment below
#      predicted the general failure a year earlier.
#   2. tests/unit/test_changelog_freshness.py (#202) walks git log, so a
#      truncated history makes it fail on data that is perfectly fresh.
#   3. hatch-vcs derives the version from git describe. With no tags in
#      reach it falls back to '0.0.0+unknown' and every 'wingman status',
#      every '--version', and every bug report filed out of a web session
#      quotes a version that does not exist. Observed: 0.0.1.dev54+unknown
#      where the real answer was 0.6.1.dev69.
#
# .github/workflows/ci.yml already sets fetch-depth: 0 for reason 2. This
# is the same agreement, kept in the other place the repo gets cloned.
#
# Order matters: unshallow BEFORE 'uv sync', or the package is built at the
# wrong version and reason 3 survives the fix.
set -euo pipefail

# A local checkout belongs to whoever made it, at whatever depth they chose.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

# Never abort the session over a network failure: a session that starts with
# a shallow clone is degraded, one that does not start at all is useless.
# Say which happened either way — a silent hook is how this stayed invisible.
if [ -f "$(git rev-parse --git-dir)/shallow" ]; then
  if git fetch --unshallow --tags --quiet 2>/dev/null; then
    echo "session-start: unshallowed ($(git rev-list --count HEAD) commits, full history)"
  else
    echo "session-start: WARNING could not unshallow — origin/main may be stale," \
         "the version string wrong, and test_changelog_freshness a false failure" >&2
  fi
else
  git fetch --tags --prune --quiet origin 2>/dev/null \
    || echo "session-start: WARNING could not fetch origin — refs may be stale" >&2
fi

# Built after the fetch above, so hatch-vcs sees the tags it derives from.
uv sync --quiet || echo "session-start: WARNING 'uv sync' failed — run it by hand" >&2
echo "session-start: ready ($(uv run python -c 'from wingman.version import wingman_version; print(wingman_version())' 2>/dev/null || echo 'version unavailable'))"
