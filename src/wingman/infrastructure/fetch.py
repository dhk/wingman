"""Read-only HTTPS fetching for public feeds (RFC-009).

The only network access in Wingman: explicit, user-invoked, read-only GETs of
public unauthenticated endpoints. No credentials are ever attached, nothing is
fetched in the background, and every failure is raised — never silently
swallowed into an empty result.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from email.message import Message
from http.client import HTTPMessage
from typing import IO

# The "Mozilla/5.0 (compatible; ...)" prefix is the convention legitimate
# crawlers use; bot filters that reject unrecognized agents outright accept
# it, while the parenthetical still identifies wingman honestly.
USER_AGENT = "Mozilla/5.0 (compatible; wingman; local-first career tool; user-invoked fetch)"
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "en"}
_TIMEOUT_SECONDS = 30
_MAX_BYTES = 20 * 1024 * 1024  # a public RSS feed is KBs; 20 MB means something is wrong
# One polite retry on rate-limit responses, honoring Retry-After up to a cap.
_RETRY_STATUSES = {429, 503}
_MAX_RETRY_AFTER_SECONDS = 10


class FetchError(Exception):
    """A public feed could not be fetched."""


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that would leave HTTPS — the initial-URL check alone
    would let a redirect downgrade the request (RFC-009)."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.startswith("https://"):
            raise FetchError(
                f"redirect to non-HTTPS URL {newurl!r} refused (RFC-009); nothing was fetched"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(HttpsOnlyRedirectHandler)


def _retry_delay(headers: Message | None) -> int:
    raw = (headers.get("Retry-After", "") if headers is not None else "").strip()
    try:
        seconds = int(raw)
    except ValueError:
        seconds = 2  # absent or HTTP-date form: a short fixed pause
    return max(1, min(seconds, _MAX_RETRY_AFTER_SECONDS))


def fetch_url(url: str) -> bytes:
    """GET a public HTTPS URL and return the response body.

    Rate-limit responses (429/503) get exactly one retry after a short,
    Retry-After-honoring pause — a single user-invoked fetch should survive
    a momentary throttle without turning into a polling loop.
    """
    if not url.startswith("https://"):
        raise FetchError(f"only https:// URLs are fetched (RFC-009); got {url!r}")
    request = urllib.request.Request(url, headers=dict(_HEADERS))  # noqa: S310
    for attempt in (1, 2):
        try:
            with _opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
                body: bytes = response.read(_MAX_BYTES + 1)
        except FetchError:
            raise
        except urllib.error.HTTPError as exc:
            if attempt == 1 and exc.code in _RETRY_STATUSES:
                time.sleep(_retry_delay(exc.headers))
                continue
            raise FetchError(f"could not fetch {url} ({exc})") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"could not fetch {url} ({exc})") from exc
        if len(body) > _MAX_BYTES:
            raise FetchError(f"{url} returned more than {_MAX_BYTES} bytes; refusing to ingest")
        return body
    raise FetchError(f"could not fetch {url} (retry exhausted)")  # pragma: no cover
