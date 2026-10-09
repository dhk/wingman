from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from wingman.infrastructure.oauth_bearer import bind_trusted_identity
from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingStore,
    PendingResult,
    onboarding_path_for,
    pending_recorder,
    pending_reference,
)
from wingman.infrastructure.operator_notify import (
    TODOIST_TASKS_URL,
    PendingNotice,
    TodoistNotifier,
    notifier_from_env,
)

ISSUER = "https://example.authkit.app"
TOKEN = "todoist-secret-token-value"


class _Post:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> int:
        self.calls.append((url, headers, json.loads(body)))
        if self.error is not None:
            raise self.error
        return self.status


def _now(job: Callable[[], None]) -> None:
    job()


def _notifier(post: _Post, project_id: str | None = None) -> TodoistNotifier:
    return TodoistNotifier(
        TOKEN,
        identities_path=Path("/srv/oauth-identities.toml"),
        project_id=project_id,
        post=post,
        run=_now,
    )


def _recorder(tmp_path: Path, notifier: TodoistNotifier | None):
    identities = tmp_path / "oauth-identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    return identities, store, pending_recorder(identities, store, notifier)


def test_first_sign_in_creates_one_task_and_repeats_do_not(tmp_path: Path) -> None:
    post = _Post()
    _identities, _store, record = _recorder(tmp_path, _notifier(post, project_id="p1"))

    assert record(ISSUER, "user_taylor") is PendingResult.ADDED
    assert record(ISSUER, "user_taylor") is PendingResult.SEEN_AGAIN

    assert len(post.calls) == 1
    url, headers, body = post.calls[0]
    assert url == TODOIST_TASKS_URL == "https://api.todoist.com/api/v1/tasks"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    ref = pending_reference(ISSUER, "user_taylor")
    assert body["content"] == f"Wingman: sign-in awaiting approval (ref {ref})"
    assert body["project_id"] == "p1"
    # Someone is locked out until approved: the task is due the day they wait.
    assert body["due_string"] == "today"
    description = str(body["description"])
    assert "example.authkit.app" in description
    assert "wingman tenant oauth-pending --identities /srv/oauth-identities.toml" in description
    assert "user_taylor" not in json.dumps(body)


def test_a_restart_does_not_renotify_an_identity_already_queued(tmp_path: Path) -> None:
    post = _Post()
    _identities, store, record = _recorder(tmp_path, _notifier(post))
    assert store.record_pending(ISSUER, "user_taylor")

    assert record(ISSUER, "user_taylor") is PendingResult.SEEN_AGAIN
    assert post.calls == []


def test_an_identity_already_bound_is_neither_queued_nor_notified(tmp_path: Path) -> None:
    post = _Post()
    identities, store, record = _recorder(tmp_path, _notifier(post))
    bind_trusted_identity(identities, ISSUER, "user_taylor", "taylor")

    assert record(ISSUER, "user_taylor") is PendingResult.ALREADY_BOUND
    assert not record(ISSUER, "user_taylor")
    assert store.pending() == []
    assert post.calls == []


def test_no_token_means_no_notifier_and_no_behaviour_change(tmp_path: Path) -> None:
    assert notifier_from_env({}, Path("/srv/ids.toml")) is None
    assert notifier_from_env({"WINGMAN_OPERATOR_TODOIST_TOKEN": "  "}, Path("/x")) is None
    _identities, store, record = _recorder(tmp_path, None)

    assert record(ISSUER, "user_taylor") is PendingResult.ADDED
    assert [item.subject for item in store.pending()] == ["user_taylor"]


def test_notifier_from_env_reads_token_and_optional_project() -> None:
    notifier = notifier_from_env(
        {
            "WINGMAN_OPERATOR_TODOIST_TOKEN": TOKEN,
            "WINGMAN_OPERATOR_TODOIST_PROJECT_ID": "p9",
        },
        Path("/srv/ids.toml"),
    )
    assert notifier is not None
    assert notifier.project_id == "p9"
    assert TOKEN not in repr(notifier)


@pytest.mark.parametrize(
    "post",
    [_Post(status=401), _Post(error=TimeoutError("timed out")), _Post(error=OSError("refused"))],
)
def test_a_failed_notification_warns_once_without_the_token_and_still_records(
    tmp_path: Path, post: _Post, caplog: pytest.LogCaptureFixture
) -> None:
    _identities, store, record = _recorder(tmp_path, _notifier(post))

    with caplog.at_level(logging.WARNING):
        assert record(ISSUER, "user_taylor") is PendingResult.ADDED

    assert [item.subject for item in store.pending()] == ["user_taylor"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Todoist" in warnings[0].getMessage()
    assert TOKEN not in caplog.text
    assert "user_taylor" not in caplog.text


def test_the_notice_carries_the_first_seen_time_stored_in_the_row(tmp_path: Path) -> None:
    # Codex review on #574: the notice used a second time.time(), so the task
    # and the oauth-pending row could disagree.
    notices: list[PendingNotice] = []

    class Capturing(TodoistNotifier):
        def notify(self, notice: PendingNotice) -> None:
            notices.append(notice)

    identities = tmp_path / "oauth-identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    record = pending_recorder(identities, store, Capturing(TOKEN, identities_path=identities))

    assert record(ISSUER, "user_taylor") is PendingResult.ADDED
    (row,) = store.pending()
    (notice,) = notices
    assert notice.first_seen == row.first_seen


def test_a_notifier_that_raises_cannot_break_recording(tmp_path: Path) -> None:
    class Exploding(TodoistNotifier):
        def notify(self, notice: PendingNotice) -> None:
            raise RuntimeError("boom")

    identities = tmp_path / "oauth-identities.toml"
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    record = pending_recorder(identities, store, Exploding(TOKEN, identities_path=identities))

    assert record(ISSUER, "user_taylor") is PendingResult.ADDED


def test_the_default_runner_does_not_block_the_caller(tmp_path: Path) -> None:
    import threading

    release = threading.Event()
    seen: list[int] = []

    def slow_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> int:
        release.wait(5)
        seen.append(200)
        return 200

    notifier = TodoistNotifier(TOKEN, identities_path=tmp_path / "ids.toml", post=slow_post)
    notifier.notify(PendingNotice(issuer=ISSUER, reference="ABCD-EFGH", first_seen=0.0))
    assert seen == []  # returned before the POST finished
    release.set()
