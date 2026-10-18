"""Coarse, best-effort capability-use dates, stored only in the tenant workspace.

The request thread only queues a bounded job. A daemon performs disk I/O; a
blocked or unwritable workspace cannot delay authentication. Dates are a
migration hint, not an audit log: shutdown/crash can lose a queued update.
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
from datetime import UTC, date, datetime
from pathlib import Path

FILENAME = "capability-url-last-used"


def last_used(workspace: Path) -> str:
    """Read without creating anything; unreadable is distinct from never recorded."""
    try:
        value = (workspace / FILENAME).read_text(encoding="ascii").strip()
        return date.fromisoformat(value).isoformat()
    except FileNotFoundError:
        return "never recorded"
    except (OSError, UnicodeError, ValueError):
        return "unavailable"


class CapabilityUsage:
    """One bounded worker and deduplicated daily jobs for a live tenant index."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Path, str]] = queue.Queue(maxsize=256)
        self._lock = threading.Lock()
        self._scheduled: dict[Path, str] = {}
        self._started = False

    def note(self, workspace: Path) -> None:
        today = datetime.now(UTC).date().isoformat()
        with self._lock:
            if self._scheduled.get(workspace) == today:
                return
            if not self._started:
                try:
                    threading.Thread(target=self._run, daemon=True, name="capability-use").start()
                except (RuntimeError, OSError):
                    return
                self._started = True
            try:
                self._jobs.put_nowait((workspace, today))
            except queue.Full:
                return
            if len(self._scheduled) >= 4096:
                self._scheduled.pop(next(iter(self._scheduled)))
            self._scheduled[workspace] = today

    def drain(self) -> None:
        """Wait for queued work (tests and explicit diagnostics, never requests)."""
        self._jobs.join()

    def _run(self) -> None:
        while True:
            workspace, today = self._jobs.get()
            success = False
            temporary: str | None = None
            try:
                if last_used(workspace) != today:
                    fd, temporary = tempfile.mkstemp(prefix=".capability-use-", dir=workspace)
                    with os.fdopen(fd, "w", encoding="ascii") as handle:
                        handle.write(today + "\n")
                    os.replace(temporary, workspace / FILENAME)
                    temporary = None
                success = True
            except (OSError, ValueError):
                # No token, path, or untrusted exception text enters a log.
                pass
            finally:
                if temporary is not None:
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        pass
                with self._lock:
                    if not success and self._scheduled.get(workspace) == today:
                        self._scheduled.pop(workspace, None)
                self._jobs.task_done()


# Shared across indexes/reloads: no idle daemon per registry/test fixture.
CAPABILITY_USAGE = CapabilityUsage()
