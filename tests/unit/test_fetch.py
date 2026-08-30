"""RFC-009: the fetch layer is HTTPS-only, including across redirects."""

import urllib.request

import pytest

from wingman.infrastructure import fetch as fetch_module
from wingman.infrastructure.fetch import FetchError, HttpsOnlyRedirectHandler, fetch_url


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests exercise retry/redirect logic, not DNS: every host resolves public."""
    monkeypatch.setattr(
        fetch_module, "_resolve", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )


def test_fetch_rejects_non_https_url() -> None:
    with pytest.raises(FetchError, match="https"):
        fetch_url("http://example.com/feed")
    with pytest.raises(FetchError, match="https"):
        fetch_url("file:///etc/passwd")


def test_redirect_to_http_is_refused() -> None:
    handler = HttpsOnlyRedirectHandler()
    request = urllib.request.Request("https://example.com/feed")
    with pytest.raises(FetchError, match="redirect"):
        handler.redirect_request(
            request, None, 301, "Moved", None, "http://insecure.example.com/feed"
        )


def test_redirect_to_https_is_allowed() -> None:
    handler = HttpsOnlyRedirectHandler()
    request = urllib.request.Request("https://example.com/feed")
    redirected = handler.redirect_request(
        request, None, 301, "Moved", None, "https://example.com/moved-feed"
    )
    assert redirected is not None
    assert redirected.full_url == "https://example.com/moved-feed"


def test_rate_limit_gets_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import email.message
    import io
    import urllib.error

    from wingman.infrastructure import fetch as fetch_module

    calls: list[str] = []
    slept: list[int] = []

    class FakeResponse:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return b"feed body"

        def geturl(self) -> str:
            return "https://rate.example.com/feed"

    class FakeOpener:
        def open(self, request: urllib.request.Request, timeout: int):  # noqa: ANN201
            calls.append(request.full_url)
            if len(calls) == 1:
                headers = email.message.Message()
                headers["Retry-After"] = "3"
                raise urllib.error.HTTPError(
                    request.full_url, 429, "Too Many Requests", headers, io.BytesIO(b"")
                )
            return FakeResponse()

    monkeypatch.setattr(fetch_module, "_opener", FakeOpener())
    monkeypatch.setattr(fetch_module.time, "sleep", slept.append)
    assert fetch_module.fetch_url("https://rate.example.com/feed") == b"feed body"
    assert len(calls) == 2
    assert slept == [3]


def test_persistent_429_fails_visibly(monkeypatch: pytest.MonkeyPatch) -> None:
    import email.message
    import io
    import urllib.error

    from wingman.infrastructure import fetch as fetch_module

    class AlwaysLimited:
        def open(self, request: urllib.request.Request, timeout: int):  # noqa: ANN201
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", email.message.Message(), io.BytesIO(b"")
            )

    monkeypatch.setattr(fetch_module, "_opener", AlwaysLimited())
    monkeypatch.setattr(fetch_module.time, "sleep", lambda _: None)
    with pytest.raises(FetchError, match="429"):
        fetch_module.fetch_url("https://rate.example.com/feed")


def test_fetch_sends_realistic_browser_headers() -> None:
    """#485: a self-identifying 'compatible; wingman' agent got hard-403'd by
    managed bot protection that a real browser sails through, so the default
    headers now look like an ordinary desktop browser's — still a read-only,
    user-invoked GET of a public page, nothing about the request is a lie
    beyond the User-Agent string itself."""
    from wingman.infrastructure.fetch import _HEADERS

    assert "wingman" not in _HEADERS["User-Agent"]
    assert "Mozilla/5.0" in _HEADERS["User-Agent"]
    assert "Accept" in _HEADERS
    assert "Accept-Language" in _HEADERS


def test_403_with_cf_mitigation_header_gets_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Cloudflare-mitigated 403 (#485) is treated like a rate limit: one
    retry, because in practice the same request often succeeds on a second
    try (the mitigation is probabilistic, not a hard wall)."""
    import email.message
    import io
    import urllib.error

    from wingman.infrastructure import fetch as fetch_module

    calls: list[str] = []

    class FakeResponse:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return b"page body"

        def geturl(self) -> str:
            return "https://cf.example.com/careers"

    class FakeOpener:
        def open(self, request: urllib.request.Request, timeout: int):  # noqa: ANN201
            calls.append(request.full_url)
            if len(calls) == 1:
                headers = email.message.Message()
                headers["cf-mitigated"] = "challenge"
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", headers, io.BytesIO(b"")
                )
            return FakeResponse()

    monkeypatch.setattr(fetch_module, "_opener", FakeOpener())
    monkeypatch.setattr(fetch_module.time, "sleep", lambda _: None)
    assert fetch_module.fetch_url("https://cf.example.com/careers") == b"page body"
    assert len(calls) == 2


def test_plain_403_without_cf_header_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary 403 (no Cloudflare mitigation marker) fails immediately —
    only the specific, probabilistic bot-challenge case earns a retry."""
    import email.message
    import io
    import urllib.error

    from wingman.infrastructure import fetch as fetch_module

    calls: list[str] = []

    class FakeOpener:
        def open(self, request: urllib.request.Request, timeout: int):  # noqa: ANN201
            calls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(b"")
            )

    monkeypatch.setattr(fetch_module, "_opener", FakeOpener())
    monkeypatch.setattr(fetch_module.time, "sleep", lambda _: None)
    with pytest.raises(FetchError, match="403"):
        fetch_module.fetch_url("https://plain.example.com/careers")
    assert len(calls) == 1
