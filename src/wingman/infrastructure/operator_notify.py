"""Tell the operator, in their own Todoist, that someone is waiting for approval.

Off unless the operator configures a token. Sends only what the operator needs
to find the row: the issuer's host, the pending reference, and when it was
first seen. Never a subject, email address, or token. Runs off the request
path; a failure is one WARNING and changes nothing else.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from wingman.infrastructure.logs import get_logger

# Todoist unified API v1 (REST v2 is superseded): POST /api/v1/tasks, Bearer
# auth, JSON body with content / description / due_string / project_id.
# due_string "today" is parsed in the token owner's own Todoist timezone. Endpoint and field
# names as used by Doist's own SDK:
# https://github.com/Doist/todoist-api-python (todoist_api_python/_core/endpoints.py,
# API_VERSION = "v1", TASKS_PATH = "tasks"); reference: https://developer.todoist.com/api/v1/
TODOIST_TASKS_URL = "https://api.todoist.com/api/v1/tasks"
TOKEN_ENV = "WINGMAN_OPERATOR_TODOIST_TOKEN"
PROJECT_ENV = "WINGMAN_OPERATOR_TODOIST_PROJECT_ID"
_TIMEOUT_SECONDS = 5.0

_logger = get_logger("operator_notify")

Post = Callable[[str, dict[str, str], bytes, float], int]
Runner = Callable[[Callable[[], None]], None]


@dataclass(frozen=True)
class PendingNotice:
    issuer: str
    reference: str
    first_seen: float


def _urllib_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed https URL
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _in_background(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="wingman-operator-notify", daemon=True).start()


class TodoistNotifier:
    def __init__(
        self,
        token: str,
        *,
        identities_path: Path,
        project_id: str | None = None,
        post: Post = _urllib_post,
        run: Runner = _in_background,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._token = token
        self.identities_path = identities_path
        self.project_id = project_id
        self._post = post
        self._run = run
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"TodoistNotifier(project_id={self.project_id!r})"

    def task(self, notice: PendingNotice) -> dict[str, str]:
        host = urlparse(notice.issuer).netloc or notice.issuer
        first = datetime.fromtimestamp(notice.first_seen, tz=UTC).isoformat(timespec="seconds")
        body = {
            "content": f"Wingman: sign-in awaiting approval (ref {notice.reference})",
            "description": (
                f"Issuer: {host}\n"
                f"First seen: {first}\n"
                "Find the row by its ref:\n"
                f"wingman tenant oauth-pending --identities {self.identities_path}"
            ),
            # Someone is locked out until this is done; it belongs on today's list.
            "due_string": "today",
        }
        if self.project_id:
            body["project_id"] = self.project_id
        return body

    def notify(self, notice: PendingNotice) -> None:
        payload = json.dumps(self.task(notice)).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        def send() -> None:
            try:
                status = self._post(TODOIST_TASKS_URL, headers, payload, self._timeout)
            except Exception as exc:  # noqa: BLE001 — best effort, never raised to admission
                _logger.warning(
                    "Todoist operator notification for ref %s failed: %s",
                    notice.reference,
                    type(exc).__name__,
                )
                return
            if not 200 <= status < 300:
                _logger.warning(
                    "Todoist operator notification for ref %s failed: HTTP %s",
                    notice.reference,
                    status,
                )

        self._run(send)


def notifier_from_env(env: Mapping[str, str], identities_path: Path) -> TodoistNotifier | None:
    """A notifier when the operator configured a token; otherwise None."""
    token = env.get(TOKEN_ENV, "").strip()
    if not token:
        return None
    project = env.get(PROJECT_ENV, "").strip() or None
    return TodoistNotifier(token, identities_path=identities_path, project_id=project)
