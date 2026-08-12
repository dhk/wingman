"""Wingman package."""

from __future__ import annotations

import os
import sys

# Never leave root-owned bytecode in a user's uv tool store (#376).
#
# Operator commands legitimately need root — reading /etc/wingman for
# 'tenant url', 'motd set', 'qotd set'. Python writes .pyc files as it
# imports, so a single `sudo wingman ...` seeds root-owned __pycache__
# directories inside the INVOKING ACCOUNT's tool store. That account can
# then no longer reinstall its own tool:
#
#     error: failed to remove directory `.../jsonschema_specifications/
#     __pycache__`: Permission denied (os error 13)
#
# On this box that reached 1,824 root-owned files before anyone noticed,
# and the failure surfaces days from its cause: a privileged command on
# Monday, an upgrade failing on Thursday naming a package nobody has heard
# of. Nothing connects the two. It is #276 a second time — that fix
# removed one suggestion to run as root; it did not make running as root
# safe.
#
# Here rather than in a console-script entry point because this module is
# imported before any wingman submodule and therefore before every
# third-party import they pull in — which is where the damage actually
# lands. An entry point runs far too late.
#
# Root only: an ordinary run keeps its bytecode cache and its startup
# speed, and the caller can still force either behaviour through
# PYTHONDONTWRITEBYTECODE, which the interpreter has already applied by
# the time this runs.
if hasattr(os, "geteuid") and os.geteuid() == 0:  # pragma: no cover - see tests
    sys.dont_write_bytecode = True

__version__ = "0.1.0"
