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
  # Inside the checkout, never /tmp. On one account here 'uv' is a snap, and a
  # strictly-confined snap has a private /tmp — so a constraints file written
  # with mktemp may be invisible to the very command it is passed to. That is
  # the leading (unproven) explanation for an install that pinned nothing while
  # reporting success (#319). The Alexandria installer writes its constraints
  # into the release directory and has never shown this.
  candidate="$REPO/.tool-constraints.txt"
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

# Verify, because passing --constraints is not the same as having been pinned.
# The whole point of this script is that the deployed versions are the tested
# ones; an install that quietly resolved something else while reporting success
# is the defect (#319), not an edge case.
[ -n "$CONSTRAINTS" ] || exit 0

say "verifying the installed versions match uv.lock"
# Reuse the constraints already exported above. Re-running the export here and
# redirecting into the same file would truncate it if that export failed,
# leaving zero pins and a cheerful "no mismatches" — the exact false success
# this block exists to prevent.
TOOL_PYTHON="$(uv tool dir 2>/dev/null)/wingman/bin/python3"
python3 - "$CONSTRAINTS" "$TOOL_PYTHON" <<'VERIFY'
import json
import re
import subprocess
import sys

constraints, tool_python = sys.argv[1], sys.argv[2]


def canonical(name: str) -> str:
    """PEP 503 normalization: '-', '_' and '.' are the same character.

    uv.lock spells these with hyphens; a wheel's own METADATA Name often
    spells them with underscores (pydantic_core, typing_extensions,
    docstring_parser). Comparing on a bare .lower() therefore matched
    neither direction: locked-and-present packages were reported "not
    installed at all", and — worse and silently — a genuinely WRONG
    version of one of them could never be reported, because the wrong-set
    below only looks at names it found (#231 deploy).
    """
    return re.sub(r"[-_.]+", "-", name).lower()
with open(constraints, encoding="utf-8") as handle:
    lines = handle.read().splitlines()

# (name, version, has_marker). A pin carrying an environment marker may legitimately
# not be installed on this platform, so only unconditional pins are required to
# be present; conditional ones are still version-checked if they are.
pins: list[tuple[str, str, bool]] = []
for line in lines:
    match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)(.*)$", line)
    if match:
        pins.append((match.group(1), match.group(2), ";" in match.group(3)))

if not pins:
    print(
        "ERROR: the constraints file holds no pins, so pinning cannot be verified.",
        file=sys.stderr,
    )
    raise SystemExit(1)

probe = (
    "import importlib.metadata as m, json;"
    "print(json.dumps({d.metadata['Name'].lower(): d.version"
    " for d in m.distributions() if d.metadata['Name']}))"
)
try:
    out = subprocess.run([tool_python, "-c", probe], capture_output=True, text=True, timeout=60)
    installed = json.loads(out.stdout)
except Exception as exc:  # noqa: BLE001 - any failure here means "cannot verify"
    print(f"==> could not read the installed versions ({exc}); pinning unverified", file=sys.stderr)
    raise SystemExit(1)

installed = {canonical(name): version for name, version in installed.items()}

wrong = [
    (name, want, installed[canonical(name)])
    for name, want, _ in pins
    if canonical(name) in installed and installed[canonical(name)] != want
]
# A locked package that is absent entirely is a broken install, not a pass.
missing = [
    name for name, _, conditional in pins if not conditional and canonical(name) not in installed
]

if wrong or missing:
    print("ERROR: the install did not honour uv.lock. These differ:", file=sys.stderr)
    for name, want, got in sorted(wrong):
        print(f"  - {name}: lock says {want}, installed {got}", file=sys.stderr)
    for name in sorted(missing):
        print(f"  - {name}: locked but not installed at all", file=sys.stderr)
    print(
        "  Constraints were passed but did not take effect. The deployed code is\n"
        "  not what was tested; refusing to report this as a successful install.",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(f"==> pinned: {len(pins)} locked packages, no mismatches")
VERIFY
