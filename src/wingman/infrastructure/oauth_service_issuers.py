"""The OAuth issuers the shared service trusts, read from its own config (#618).

One WorkOS user has the same full `sub` under the browser-session issuer and
the AuthKit MCP issuer (#582), so a manual `oauth-bind` / `oauth-approve`
binds the subject under every issuer the service trusts. That set must be the
SAME one the running service uses, never a value typed on a command line, so
it is read here from the env files the `wingman-mcp` unit loads:

- the OAuth file (`/etc/wingman/oauth.env`, or the path saved in
  `/etc/wingman/oauth-config-path`, exactly as `wingman-add-tenant.sh`
  resolves it): `--oauth-issuer` inside `WINGMAN_OAUTH_ARGS`;
- the browser-login file (`/etc/wingman/oauth-web.env`): `--oauth-web-client-id`
  inside `WINGMAN_OAUTH_WEB_ARGS`, from which the session issuer follows.

`oauth-approve` runs as root through plain `sudo`, whose environment never
loads the service's env files, so they are parsed here as data: never sourced,
and only the named keys are kept. The browser file also holds the client
secret; nothing read from either file other than an issuer URL or a client id
ever reaches a message, a log line or an exception.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

#: Module globals resolved at call time, so the test suite can point them at
#: a scratch directory (tests/conftest.py) and a real box's files are never read.
OAUTH_CONFIG_POINTER = Path("/etc/wingman/oauth-config-path")
DEFAULT_OAUTH_CONFIG_PATH = Path("/etc/wingman/oauth.env")
DEFAULT_OAUTH_WEB_CONFIG_PATH = Path("/etc/wingman/oauth-web.env")

_OAUTH_ARGS = "WINGMAN_OAUTH_ARGS"
_OAUTH_ISSUER = "WINGMAN_OAUTH_ISSUER"
_OAUTH_WEB_ARGS = "WINGMAN_OAUTH_WEB_ARGS"
_ISSUER_FLAG = "--oauth-issuer"
_CLIENT_ID_FLAG = "--oauth-web-client-id"


class ServiceIssuersError(Exception):
    """The service's trusted issuers cannot be determined; nothing may be bound."""


@dataclass(frozen=True)
class ServiceIssuers:
    """What the shared service accepts: the MCP issuer, and the browser one if on."""

    mcp: str
    browser: str | None

    @property
    def all(self) -> tuple[str, ...]:
        return (self.mcp,) if self.browser is None else (self.mcp, self.browser)

    def for_binding(self, issuer: str) -> tuple[str, ...]:
        """The given issuer first, then every other trusted one; refuse a stranger."""
        if issuer not in self.all:
            raise ServiceIssuersError(
                f"issuer {issuer!r} is not one of the issuers the shared service trusts "
                f"({', '.join(self.all)}); nothing was bound"
            )
        return (issuer, *(other for other in self.all if other != issuer))


def _read_text(path: Path, label: str) -> str | None:
    """The file's text; None when it is absent. Unreadable is its own error."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        from wingman.infrastructure.broadcast import permission_problem

        detail = permission_problem(path) or f"Cannot read {path} — permission denied."
        raise ServiceIssuersError(
            f"the shared service's {label} cannot be read, so its trusted issuers are "
            f"unknown; nothing was bound.\n{detail}"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        # The class only: a decode error can quote the file's bytes.
        raise ServiceIssuersError(
            f"the shared service's {label} {path} could not be read "
            f"({type(exc).__name__}); nothing was bound"
        ) from None


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_env_keys(text: str, names: set[str]) -> dict[str, str]:
    """`NAME=value` entries for `names` only, the way systemd's EnvironmentFile=
    reads them: comments and other keys skipped, outer quotes removed, last
    assignment wins. Lines that are not assignments are ignored, never echoed."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line[0] in "#;":
            continue
        name, separator, value = line.partition("=")
        if not separator or name.strip() not in names:
            continue
        values[name.strip()] = _unquote(value.strip())
    return values


def _split_like_systemd(args: str, *, key: str, path: Path) -> list[str]:
    """`args` as the words the service receives from an unquoted `$VAR`.

    systemd splits at whitespace and honours and removes single and double
    quotes, so `'client_01X'` reaches the service as `client_01X`. Plain and
    quoted words are matched exactly here. Backslash escapes and unbalanced
    quotes are where the two parsers could differ, so they are refused rather
    than guessed at. The message names the key, never the string.
    """
    unsupported = ServiceIssuersError(
        f"{key} in {path} uses argument syntax this command cannot read exactly as the "
        "shared service does (a backslash or an unbalanced quote), so its trusted "
        "issuers are unknown; nothing was bound"
    )
    if "\\" in args:
        raise unsupported
    lexer = shlex.shlex(args, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        raise unsupported from None


def _flag_value(args: str, flag: str, *, key: str, path: Path) -> str:
    """The single value given to `flag` in the service's args string.

    Missing, empty, repeated with different values, or a value that is not one
    plain word (whitespace or a quote left in it) is refused; the message names
    the flag and key, never the string itself.
    """
    words = _split_like_systemd(args, key=key, path=path)
    found: list[str] = []
    for position, word in enumerate(words):
        if word == flag and position + 1 < len(words):
            found.append(words[position + 1])
        elif word.startswith(flag + "="):
            found.append(word.split("=", 1)[1])
    if (
        not found
        or not all(found)
        or len(set(found)) != 1
        or any(char.isspace() or char in "\"'" for char in found[0])
    ):
        raise ServiceIssuersError(
            f"{key} in {path} does not give exactly one {flag} value, so the shared "
            "service's trusted issuers are unknown; nothing was bound"
        )
    return found[0]


def _oauth_config_path() -> Path:
    """`wingman-add-tenant.sh`'s resolution: the saved pointer, else the default."""
    text = _read_text(OAUTH_CONFIG_POINTER, "OAuth config pointer")
    if text is None:
        return DEFAULT_OAUTH_CONFIG_PATH
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].strip() or not lines[0].strip().startswith("/"):
        raise ServiceIssuersError(
            f"saved OAuth config pointer {OAUTH_CONFIG_POINTER} must contain exactly one "
            "absolute path; nothing was bound"
        )
    return Path(lines[0].strip())


def read_service_issuers(
    oauth_config: Path | None = None, oauth_web_config: Path | None = None
) -> ServiceIssuers:
    """The issuers the shared service trusts, or ServiceIssuersError (fail closed).

    `oauth_config` / `oauth_web_config` override the default locations, for a
    box provisioned with WINGMAN_SHARED_OAUTH_CONFIG / _WEB_CONFIG; they name
    files, never issuers.
    """
    from wingman.infrastructure.oauth_bearer import OAuthConfigError, _https_or_loopback
    from wingman.infrastructure.oauth_browser import workos_session_issuer

    oauth_path = oauth_config if oauth_config is not None else _oauth_config_path()
    text = _read_text(oauth_path, "OAuth config")
    if text is None:
        raise ServiceIssuersError(
            f"the shared service's OAuth config {oauth_path} was not found, so its trusted "
            "issuers are unknown; nothing was bound. Pass --oauth-config if this box keeps "
            "it elsewhere."
        )
    values = read_env_keys(text, {_OAUTH_ARGS, _OAUTH_ISSUER})
    if _OAUTH_ARGS not in values:
        raise ServiceIssuersError(
            f"{oauth_path} has no {_OAUTH_ARGS}, so the shared service is not serving OAuth; "
            "nothing was bound"
        )
    mcp = _flag_value(values[_OAUTH_ARGS], _ISSUER_FLAG, key=_OAUTH_ARGS, path=oauth_path)
    if _OAUTH_ISSUER in values and values[_OAUTH_ISSUER] != mcp:
        raise ServiceIssuersError(
            f"{_OAUTH_ISSUER} and the {_ISSUER_FLAG} in {_OAUTH_ARGS} disagree in {oauth_path}; "
            "nothing was bound"
        )

    web_path = oauth_web_config if oauth_web_config is not None else DEFAULT_OAUTH_WEB_CONFIG_PATH
    browser: str | None = None
    web_text = _read_text(web_path, "browser-login config")
    if web_text is None and oauth_web_config is not None:
        raise ServiceIssuersError(
            f"the browser-login config {web_path} was not found; nothing was bound"
        )
    if web_text is not None:
        web_values = read_env_keys(web_text, {_OAUTH_WEB_ARGS})
        if _OAUTH_WEB_ARGS in web_values:
            client_id = _flag_value(
                web_values[_OAUTH_WEB_ARGS], _CLIENT_ID_FLAG, key=_OAUTH_WEB_ARGS, path=web_path
            )
            browser = workos_session_issuer(client_id)
    try:
        for issuer in (mcp, browser):
            if issuer is not None:
                _https_or_loopback(issuer, "configured issuer")
    except OAuthConfigError as exc:
        raise ServiceIssuersError(f"{exc}; nothing was bound") from exc
    return ServiceIssuers(mcp=mcp, browser=browser)
