"""RFC-009: the fetch layer is HTTPS-only, including across redirects."""

import urllib.request

import pytest

from wingman.infrastructure.fetch import FetchError, HttpsOnlyRedirectHandler, fetch_url


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
