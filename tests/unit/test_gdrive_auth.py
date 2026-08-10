"""RFC-053 (#205): the device-flow state machine (start/pending/finish/
expired/denied), credential file isolation, and the client-id/secret
resolution ladder — all against a scripted HTTP layer, no real Google API
calls (matching this repo's ScriptedProvider testing convention)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from wingman.infrastructure.gdrive_auth import (
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    GDriveAuthError,
    access_token,
    clear_credentials,
    credentials_path,
    drive_auth,
    is_authorized,
    pending_path,
    read_credentials,
    resolve_client_id,
    resolve_client_secret,
)


class ScriptedPoster:
    """Returns canned (status, body) responses in call order; records every
    (url, data) it was given."""

    def __init__(self, responses: list[tuple[int, dict[str, Any]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, data))
        if not self._responses:
            raise AssertionError("ScriptedPoster ran out of scripted responses")
        return self._responses.pop(0)


DEVICE_START_RESPONSE = (
    200,
    {
        "device_code": "dc-123",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://www.google.com/device",
        "expires_in": 1800,
        "interval": 5,
    },
)


def test_start_writes_pending_file_and_returns_code_and_url(tmp_path: Path) -> None:
    home = tmp_path / "home"
    poster = ScriptedPoster([DEVICE_START_RESPONSE])

    result = drive_auth(home=home, client_id="cid", client_secret="secret", poster=poster)

    assert result.status == "started"
    assert result.user_code == "ABCD-EFGH"
    assert result.verification_url == "https://www.google.com/device"
    assert "ABCD-EFGH" in result.detail
    assert pending_path(home).exists()
    pending = json.loads(pending_path(home).read_text(encoding="utf-8"))
    assert pending["device_code"] == "dc-123"
    assert pending["user_code"] == "ABCD-EFGH"
    assert len(poster.calls) == 1
    assert poster.calls[0][1]["client_id"] == "cid"
    assert not credentials_path(home).exists()


def test_start_raises_on_malformed_response(tmp_path: Path) -> None:
    home = tmp_path / "home"
    poster = ScriptedPoster([(400, {"error": "invalid_client"})])

    with pytest.raises(GDriveAuthError, match="invalid_client"):
        drive_auth(home=home, client_id="cid", client_secret="secret", poster=poster)
    assert not pending_path(home).exists()


def test_finish_authorization_pending_keeps_waiting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    start_poster = ScriptedPoster([DEVICE_START_RESPONSE])
    drive_auth(home=home, client_id="cid", client_secret="secret", poster=start_poster)

    poll_poster = ScriptedPoster([(400, {"error": "authorization_pending"})])
    result = drive_auth(home=home, client_id="cid", client_secret="secret", poster=poll_poster)

    assert result.status == "pending"
    assert result.user_code == "ABCD-EFGH"
    assert pending_path(home).exists()  # kept — the caller polls again later
    assert not credentials_path(home).exists()


def test_finish_slow_down_bumps_interval_and_keeps_waiting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    before = json.loads(pending_path(home).read_text(encoding="utf-8"))

    result = drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(400, {"error": "slow_down"})]),
    )

    assert result.status == "pending"
    after = json.loads(pending_path(home).read_text(encoding="utf-8"))
    assert after["interval"] > before["interval"]


def test_finish_success_writes_credentials_and_clears_pending(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )

    result = drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster(
            [(200, {"access_token": "at-1", "refresh_token": "rt-1", "scope": "drive.file"})]
        ),
    )

    assert result.status == "authorized"
    assert not pending_path(home).exists()
    creds = read_credentials(home)
    assert creds is not None
    assert creds["refresh_token"] == "rt-1"
    assert is_authorized(home)


def test_credentials_file_is_mode_0600(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(200, {"access_token": "at-1", "refresh_token": "rt-1"})]),
    )
    mode = stat.S_IMODE(credentials_path(home).stat().st_mode)
    assert mode == 0o600


def test_finish_access_denied_clears_pending(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )

    result = drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(400, {"error": "access_denied"})]),
    )

    assert result.status == "denied"
    assert not pending_path(home).exists()
    assert not credentials_path(home).exists()


def test_finish_expired_token_from_google_clears_pending(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )

    result = drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(400, {"error": "expired_token"})]),
    )

    assert result.status == "expired"
    assert not pending_path(home).exists()


def test_finish_locally_expired_never_calls_google(tmp_path: Path) -> None:
    """Our own bookkeeping of expires_at catches an old code before wasting
    a poll on Google's endpoint at all."""
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    pending = json.loads(pending_path(home).read_text(encoding="utf-8"))
    pending["expires_at"] = 0.0  # already expired, long ago
    pending_path(home).write_text(json.dumps(pending), encoding="utf-8")

    poller = ScriptedPoster([])  # must never be called
    result = drive_auth(home=home, client_id="cid", client_secret="secret", poster=poller)

    assert result.status == "expired"
    assert poller.calls == []
    assert not pending_path(home).exists()


def test_finish_unrecognized_error_raises_and_clears_pending(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )

    with pytest.raises(GDriveAuthError, match="server_error"):
        drive_auth(
            home=home,
            client_id="cid",
            client_secret="secret",
            poster=ScriptedPoster([(500, {"error": "server_error"})]),
        )
    assert not pending_path(home).exists()


def test_is_authorized_false_until_credentials_exist(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert is_authorized(home) is False
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    assert is_authorized(home) is False  # started, not finished
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(200, {"access_token": "at-1", "refresh_token": "rt-1"})]),
    )
    assert is_authorized(home) is True


def test_clear_credentials_removes_the_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(200, {"access_token": "at-1", "refresh_token": "rt-1"})]),
    )
    assert is_authorized(home)
    clear_credentials(home)
    assert not is_authorized(home)


def test_two_accounts_have_fully_isolated_credentials(tmp_path: Path) -> None:
    """Home-dir scoping (RFC-046's own pattern) is what gives dhk/trent
    isolation for free — proven directly, not assumed."""
    dhk_home = tmp_path / "dhk"
    trent_home = tmp_path / "trent"

    drive_auth(
        home=dhk_home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    drive_auth(
        home=dhk_home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(200, {"access_token": "at-dhk", "refresh_token": "rt-dhk"})]),
    )

    assert is_authorized(dhk_home)
    assert not is_authorized(trent_home)
    dhk_creds = read_credentials(dhk_home)
    assert dhk_creds is not None
    assert dhk_creds["refresh_token"] == "rt-dhk"
    assert read_credentials(trent_home) is None


def test_access_token_raises_when_never_authorized(tmp_path: Path) -> None:
    home = tmp_path / "home"
    with pytest.raises(GDriveAuthError, match="not authorized"):
        access_token(home=home, poster=ScriptedPoster([]))


def test_access_token_refreshes_using_stored_refresh_token(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(200, {"access_token": "unused", "refresh_token": "rt-stored"})]),
    )

    refresh_poster = ScriptedPoster([(200, {"access_token": "fresh-at-1"})])
    token = access_token(home=home, client_id="cid", client_secret="secret", poster=refresh_poster)

    assert token == "fresh-at-1"
    assert refresh_poster.calls[0][1]["refresh_token"] == "rt-stored"
    assert refresh_poster.calls[0][1]["grant_type"] == "refresh_token"


def test_access_token_raises_when_refresh_fails(tmp_path: Path) -> None:
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(200, {"access_token": "unused", "refresh_token": "rt-stored"})]),
    )

    with pytest.raises(GDriveAuthError, match="invalid_grant"):
        access_token(
            home=home,
            client_id="cid",
            client_secret="secret",
            poster=ScriptedPoster([(400, {"error": "invalid_grant"})]),
        )


def test_resolve_client_id_env_wins_over_host_file_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cleared first: this asserts the DEFAULT branch, and the variable is set
    # for real on any host actually configured for Drive — so the test failed
    # precisely where the feature works. monkeypatch restores it afterwards.
    monkeypatch.delenv("WINGMAN_GDRIVE_CLIENT_ID", raising=False)

    home = tmp_path / "home"
    assert resolve_client_id(home) == DEFAULT_CLIENT_ID

    wingman_env = home / ".config" / "wingman" / "wingman.env"
    wingman_env.parent.mkdir(parents=True)
    wingman_env.write_text("WINGMAN_GDRIVE_CLIENT_ID=from-host-file\n", encoding="utf-8")
    assert resolve_client_id(home) == "from-host-file"

    monkeypatch.setenv("WINGMAN_GDRIVE_CLIENT_ID", "from-env")
    assert resolve_client_id(home) == "from-env"


def test_resolve_client_secret_env_wins_over_host_file_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cleared first: this asserts the DEFAULT branch, and the variable is set
    # for real on any host actually configured for Drive — so the test failed
    # precisely where the feature works. monkeypatch restores it afterwards.
    monkeypatch.delenv("WINGMAN_GDRIVE_CLIENT_SECRET", raising=False)

    home = tmp_path / "home"
    assert resolve_client_secret(home) == DEFAULT_CLIENT_SECRET

    wingman_env = home / ".config" / "wingman" / "wingman.env"
    wingman_env.parent.mkdir(parents=True)
    wingman_env.write_text("WINGMAN_GDRIVE_CLIENT_SECRET=from-host-file\n", encoding="utf-8")
    assert resolve_client_secret(home) == "from-host-file"

    monkeypatch.setenv("WINGMAN_GDRIVE_CLIENT_SECRET", "from-env")
    assert resolve_client_secret(home) == "from-env"


def test_default_poster_wraps_a_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercises _default_poster's own exception handling (not the injected
    fake) — confirms a real urllib failure surfaces as GDriveAuthError,
    never a raw urllib exception, without making a real network call."""
    import urllib.error

    from wingman.infrastructure import gdrive_auth as gdrive_auth_module

    def boom(*args: object, **kwargs: object) -> object:
        raise urllib.error.URLError("simulated network failure")

    monkeypatch.setattr(gdrive_auth_module.urllib.request, "urlopen", boom)

    with pytest.raises(GDriveAuthError, match="simulated network failure"):
        gdrive_auth_module._default_poster("https://oauth2.googleapis.com/device/code", {})


def test_slow_down_actually_slows_the_next_poll_down(tmp_path: Path) -> None:
    """Google says slow_down; the interval was bumped and stored, and then
    nothing ever read it before posting again — so every subsequent call
    polled just as fast, which is what earned the slow_down (#231 review)."""
    home = tmp_path / "home"
    drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([DEVICE_START_RESPONSE]),
    )
    told_off = drive_auth(
        home=home,
        client_id="cid",
        client_secret="secret",
        poster=ScriptedPoster([(400, {"error": "slow_down"})]),
    )
    assert told_off.status == "pending"

    # The next call must answer locally rather than posting again. An empty
    # script would raise if it tried.
    backed_off = drive_auth(
        home=home, client_id="cid", client_secret="secret", poster=ScriptedPoster([])
    )

    assert backed_off.status == "pending"
    assert "poll less often" in backed_off.detail
