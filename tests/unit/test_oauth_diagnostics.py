"""Privacy and verified-token integration for the temporary #582 probe."""

import json
import logging
import time
from io import BytesIO
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from wingman.infrastructure import oauth_diagnostics as diagnostic
from wingman.infrastructure.oauth_bearer import (
    BearerError,
    IdentityMap,
    OAuthSettings,
    validate_access_token,
)
from wingman.infrastructure.oauth_browser import (
    OAuthBrowserSessions,
    OAuthBrowserSettings,
    validate_browser_session_token,
)
from wingman.infrastructure.tenants import TenantIndex


@pytest.fixture(autouse=True)
def reset_probe(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("WINGMAN_OAUTH_IDENTITY_DIAGNOSTICS", "1")
    diagnostic._seen.clear()
    # No test may reach the network: drop scheduled userinfo probes unless a
    # test installs its own runner.
    monkeypatch.setattr(diagnostic, "_run", lambda _job: None)
    caplog.set_level(logging.INFO, logger="wingman.oauth_diagnostics")


def test_default_off(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("WINGMAN_OAUTH_IDENTITY_DIAGNOSTICS")
    diagnostic.observe_verified("mcp", {"sub": "private-sub"})
    diagnostic.observe_exchange_user({"user": {"email_verified": True}}, "private-sub")
    assert not caplog.records
    assert not diagnostic._seen


def test_private_values_and_unknown_names_never_emitted(caplog: pytest.LogCaptureFixture) -> None:
    claims = {
        "sub": "private-sub",
        "email": "private@example.test",
        "email_verified": "true",
        "urn:wingman:email_verified": True,
        "secret-disguised-as-claim-name": "secret-token",
    }
    diagnostic.observe_verified("mcp", claims)
    diagnostic.observe_verified("browser", claims)
    first, second = [json.loads(r.message.split(" ", 1)[1]) for r in caplog.records]
    assert first["email_verified"] is None
    assert first["custom_email_verified"] is True
    assert first["other_claim_count"] == 1
    assert second["same_subject_seen_on_other_surface"] is True
    for secret in ("private-sub", "private@example.test", "secret-token", "secret-disguised"):
        assert secret not in caplog.text
        assert secret not in repr(diagnostic._seen)


def test_distinct_subject_expiry_bound_and_dedup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: 10.0)
    diagnostic.observe_verified("mcp", {"sub": "one"})
    diagnostic.observe_verified("mcp", {"sub": "one"})
    diagnostic.observe_verified("browser", {"sub": "two"})
    assert len(caplog.records) == 2
    assert '"same_subject_seen_on_other_surface": false' in caplog.records[-1].message
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: 611.0)
    diagnostic.observe_verified("browser", {"sub": "one"})
    assert '"same_subject_seen_on_other_surface": false' in caplog.records[-1].message
    for i in range(100):
        diagnostic.observe_verified("mcp", {"sub": str(i)})
    assert len(diagnostic._seen) == diagnostic._LIMIT


def test_exchange_user_missing_malformed_and_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    for user in (None, "bad", {"id": "wrong", "email_verified": 1, "email": "private"}):
        diagnostic.observe_exchange_user({"user": user}, "expected")
    assert '"user_id_matches_verified_sub": false' in caplog.records[-1].message
    assert '"email_verified": null' in caplog.records[-1].message
    assert "private" not in caplog.text


def test_actual_validators_and_exchange_emit_only_after_validation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    browser = OAuthBrowserSettings(
        "client_test",
        "https://api.workos.com/user_management/authorize",
        "https://api.workos.com/user_management/authenticate",
        "https://example.test/oauth/callback",
    )
    mcp = OAuthSettings(
        "https://example.authkit.app", "https://example.test/mcp", "https://jwks.test"
    )
    common = {"sub": "private-sub", "exp": int(time.time()) + 300}
    browser_token = jwt.encode(
        {**common, "iss": browser.session_issuer, "client_id": browser.client_id},
        key,
        algorithm="RS256",
    )
    mcp_token = jwt.encode(
        {**common, "iss": mcp.issuer, "aud": mcp.audience}, key, algorithm="RS256"
    )
    bad_token = jwt.encode(
        {**common, "iss": "https://wrong.test", "aud": mcp.audience}, key, algorithm="RS256"
    )
    resolver = lambda _token: key.public_key()  # noqa: E731
    with pytest.raises(BearerError):
        validate_access_token(bad_token, mcp, resolver)
    with pytest.raises(BearerError):
        validate_browser_session_token(bad_token, browser, resolver)
    assert not caplog.records
    validate_access_token(mcp_token, mcp, resolver)
    monkeypatch.setenv("WINGMAN_OAUTH_WEB_CLIENT_SECRET", "private-secret")
    payload = {
        "access_token": browser_token,
        "refresh_token": "private-refresh",
        "user": {"id": "private-sub", "email": "private-email", "email_verified": True},
    }
    response = MagicMock()
    response.__enter__.return_value = BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: response)
    sessions = OAuthBrowserSessions(browser, mcp, IdentityMap({}), TenantIndex([]), resolver)
    assert sessions._exchange_code("private-code", "private-verifier") == browser_token
    assert '"surface": "mcp"' in caplog.text
    assert '"same_subject_seen_on_other_surface": true' in caplog.text
    assert '"user_id_matches_verified_sub": true' in caplog.text
    assert '"email_verified": true' in caplog.text
    for private in (
        browser_token,
        mcp_token,
        "private-sub",
        "private-email",
        "private-refresh",
        "private-code",
        "private-secret",
        "private-verifier",
    ):
        assert private not in caplog.text


# --- userinfo probe (MCP surface) --------------------------------------------

ISSUER = "https://example.authkit.app"
TOKEN = "private-access-token"


class _Requests:
    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, tuple)
        return answer


def _userinfo_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    out = []
    for record in caplog.records:
        body = json.loads(record.getMessage().split(" ", 1)[1])
        if body.get("surface") == "mcp_userinfo":
            out.append(body)
    return out


def _probe_world(monkeypatch: pytest.MonkeyPatch, requests: _Requests) -> list[object]:
    jobs: list[object] = []
    monkeypatch.setattr(diagnostic, "_request", requests)
    monkeypatch.setattr(diagnostic, "_run", jobs.append)
    return jobs


def test_userinfo_probe_runs_off_the_request_path_and_logs_names_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    body = {
        "sub": "private-sub",
        "email": "private@example.test",
        "email_verified": True,
        "name": "Private Name",
        "secret-disguised-as-field": "private-extra",
    }
    requests = _Requests((200, json.dumps(body).encode()))
    jobs = _probe_world(monkeypatch, requests)

    diagnostic.observe_verified("mcp", {"sub": "private-sub"}, token=TOKEN, issuer=ISSUER)

    # Nothing is sent until the background runner runs the job.
    assert requests.calls == []
    assert len(jobs) == 1
    jobs[0]()  # type: ignore[operator]

    method, url, headers, timeout = requests.calls[0]
    assert (method, url, timeout) == ("GET", f"{ISSUER}/oauth2/userinfo", 5.0)
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    [line] = _userinfo_lines(caplog)
    assert line == {
        "surface": "mcp_userinfo",
        "status": 200,
        "field_names": ["email", "email_verified", "name", "sub"],
        "other_field_count": 1,
        "email_verified": True,
        "sub_matches_verified_sub": True,
    }
    for private in (TOKEN, "private-sub", "private@example.test", "Private Name", "private-extra"):
        assert private not in caplog.text


def test_userinfo_probe_is_off_by_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("WINGMAN_OAUTH_IDENTITY_DIAGNOSTICS")
    requests = _Requests()
    jobs = _probe_world(monkeypatch, requests)
    diagnostic.observe_verified("mcp", {"sub": "private-sub"}, token=TOKEN, issuer=ISSUER)
    assert jobs == [] and requests.calls == [] and not caplog.records


def test_userinfo_probe_once_per_subject_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _Requests()
    jobs = _probe_world(monkeypatch, requests)
    diagnostic.observe_verified("mcp", {"sub": "same"}, token=TOKEN, issuer=ISSUER)
    diagnostic.observe_verified("mcp", {"sub": "same"}, token=TOKEN, issuer=ISSUER)
    diagnostic.observe_verified("browser", {"sub": "same"})
    assert len(jobs) == 1


def test_userinfo_probe_retries_once_with_post_on_405(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    requests = _Requests(
        (405, b""), (200, json.dumps({"sub": "other", "email_verified": "true"}).encode())
    )
    jobs = _probe_world(monkeypatch, requests)
    diagnostic.observe_verified("mcp", {"sub": "private-sub"}, token=TOKEN, issuer=ISSUER)
    jobs[0]()  # type: ignore[operator]
    assert [c[0] for c in requests.calls] == ["GET", "POST"]
    [line] = _userinfo_lines(caplog)
    assert line["status"] == 200
    assert line["email_verified"] is None  # a string is not a strict boolean
    assert line["sub_matches_verified_sub"] is False


def test_userinfo_probe_failure_logs_status_or_exception_class_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    requests = _Requests((401, b'{"error": "private-detail"}'), TimeoutError("private-detail"))
    jobs = _probe_world(monkeypatch, requests)
    diagnostic.observe_verified("mcp", {"sub": "one"}, token=TOKEN, issuer=ISSUER)
    diagnostic.observe_verified("mcp", {"sub": "two"}, token=TOKEN, issuer=ISSUER)
    for job in jobs:
        job()  # type: ignore[operator]
    first, second = _userinfo_lines(caplog)
    assert first["status"] == 401 and first["field_names"] == []
    assert second["status"] == "TimeoutError" and second["field_names"] == []
    assert "private-detail" not in caplog.text


def test_userinfo_probe_never_sends_the_token_off_an_https_issuer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    requests = _Requests()
    jobs = _probe_world(monkeypatch, requests)
    diagnostic.observe_verified(
        "mcp", {"sub": "private-sub"}, token=TOKEN, issuer="http://example.authkit.app"
    )
    for job in jobs:
        job()  # type: ignore[operator]
    assert requests.calls == []
    [line] = _userinfo_lines(caplog)
    assert line["status"] == "refused-issuer"


def test_validate_access_token_hands_the_probe_its_token_and_issuer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    mcp = OAuthSettings(ISSUER, "https://example.test/mcp", "https://jwks.test")
    token = jwt.encode(
        {"sub": "private-sub", "exp": int(time.time()) + 300, "iss": ISSUER, "aud": mcp.audience},
        key,
        algorithm="RS256",
    )
    requests = _Requests((200, b'{"sub": "private-sub"}'))
    jobs = _probe_world(monkeypatch, requests)
    validate_access_token(token, mcp, lambda _t: key.public_key())
    jobs[0]()  # type: ignore[operator]
    assert requests.calls[0][1] == f"{ISSUER}/oauth2/userinfo"
    assert requests.calls[0][2]["Authorization"] == f"Bearer {token}"
    assert token not in caplog.text
