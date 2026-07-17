"""Read-only HTTPS fetching for public feeds (RFC-009).

The only network access in Wingman: explicit, user-invoked, read-only GETs of
public unauthenticated endpoints. No credentials are ever attached, nothing is
fetched in the background, and every failure is raised — never silently
swallowed into an empty result.
"""

from __future__ import annotations

import urllib.error
import urllib.request

USER_AGENT = "wingman (local-first career tool; explicit user-invoked fetch)"
_TIMEOUT_SECONDS = 30
_MAX_BYTES = 20 * 1024 * 1024  # a public RSS feed is KBs; 20 MB means something is wrong


class FetchError(Exception):
    """A public feed could not be fetched."""


def fetch_url(url: str) -> bytes:
    """GET a public HTTPS URL and return the response body."""
    if not url.startswith("https://"):
        raise FetchError(f"only https:// URLs are fetched (RFC-009); got {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            body: bytes = response.read(_MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"could not fetch {url} ({exc})") from exc
    if len(body) > _MAX_BYTES:
        raise FetchError(f"{url} returned more than {_MAX_BYTES} bytes; refusing to ingest")
    return body
