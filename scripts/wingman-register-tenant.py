#!/usr/bin/python3 -I
"""wingman-register-tenant — append ONE unprivileged tenant to the registry.

Installed root:root under /usr/local/libexec/wingman/ and run through a
one-line sudoers rule by the shared service's account (RFC-086, #658). It is
the only root-run code on that path, and it exists so the tenant registry,
`/etc/wingman/tenants.toml`, can stay root-owned while an approval is done
from chat.

Two properties matter more than the feature:

* It is STANDALONE: standard library only, no `import wingman`. The shared
  service's own install lives in a directory that account can write, so
  running that code as root would hand the account root. Nothing under that
  install is ever executed here.
* It can do exactly one thing: add `[[tenant]] slug/data_dir` with no flags.
  `privileged`, `funded`, `super_user` and `capability_url_retired` are
  never written, so the worst a caller who bypasses every check above this
  can obtain is an empty, unfunded, unprivileged workspace. The slug is the
  only argument; paths come from a root-owned config file, never from the
  caller; the workspace must already exist as a real directory owned by the
  service account.

Usage (the only form): wingman-register-tenant <slug>
Exit: 0 registered or already registered; 1 refused (nothing changed);
2 misuse.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import stat
import sys
import tempfile

try:  # Python 3.11+. Older systems fall back to the line check below.
    import tomllib
except ImportError:  # pragma: no cover - depends on the host's Python
    tomllib = None  # type: ignore[assignment]

CONF_PATH = "/etc/wingman/register-helper.conf"
# Same pattern as wingman.infrastructure.oauth_onboarding._SLUG. A test keeps
# the two equal; this file cannot import it (see the module docstring).
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
KEYS = ("REGISTRY", "DATA_ROOT", "SERVICE_USER")


class Refused(Exception):
    """A reason nothing was changed. The message is shown to the operator."""


def read_conf(conf_path: str, trusted_uid: int = 0) -> dict[str, str]:
    """The three settings, from a file only `trusted_uid` can have written.

    A config the service account could edit would let it name any registry
    or workspace root, which is the whole boundary, so a file that is not
    owned by root or is writable by anybody else is refused outright.
    """
    try:
        info = os.stat(conf_path)
    except OSError as exc:
        raise Refused(f"config {conf_path} could not be read: {exc.strerror}") from exc
    if info.st_uid != trusted_uid or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise Refused(f"config {conf_path} must be owned by root and writable by no one else")
    values: dict[str, str] = {}
    with open(conf_path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or key.strip() not in KEYS:
                raise Refused(f"config {conf_path} has a line this helper does not understand")
            values[key.strip()] = value.strip()
    missing = [key for key in KEYS if not values.get(key)]
    if missing:
        raise Refused(f"config {conf_path} is missing: {', '.join(missing)}")
    for key in ("REGISTRY", "DATA_ROOT"):
        if not os.path.isabs(values[key]):
            raise Refused(f"config {key} must be an absolute path")
    return values


def _workspace(slug: str, data_root: str, service_user: str) -> str:
    """The slug's workspace, proven to be a real one the service account made."""
    try:
        account = pwd.getpwnam(service_user)
    except KeyError as exc:
        raise Refused(f"service account {service_user!r} does not exist") from exc
    data_dir = os.path.join(data_root, slug)
    try:
        info = os.lstat(data_dir)
    except OSError as exc:
        raise Refused(f"workspace {data_dir} does not exist yet; nothing was registered") from exc
    if not stat.S_ISDIR(info.st_mode) or os.path.realpath(data_dir) != data_dir:
        raise Refused(f"workspace {data_dir} is not a plain directory (no links); refused")
    if info.st_uid != account.pw_uid:
        raise Refused(f"workspace {data_dir} is not owned by {service_user!r}; refused")
    if not os.path.isfile(os.path.join(data_dir, "wingman.db")):
        raise Refused(f"workspace {data_dir} has no wingman.db; nothing was registered")
    return data_dir


def _existing(text: str, slug: str, data_dir: str) -> str | None:
    """None if absent; 'same' if an identical flagless entry exists; else raise."""
    if tomllib is not None:
        try:
            entries = tomllib.loads(text).get("tenant", [])
        except tomllib.TOMLDecodeError as exc:
            raise Refused(f"the registry could not be parsed ({exc}); nothing changed") from exc
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("slug") == slug:
                flags = {"privileged", "funded", "super_user", "capability_url_retired"}
                if entry.get("data_dir") == data_dir and not (flags & set(entry)):
                    return "same"
                raise Refused(
                    f"tenant {slug!r} already exists with other settings; nothing changed"
                )
        return None
    pattern = re.compile(
        r"^\s*slug\s*=\s*[\"']" + re.escape(slug) + r"[\"']\s*(#.*)?$", re.MULTILINE
    )
    if pattern.search(text):
        raise Refused(f"tenant {slug!r} is already in the registry; nothing changed")
    return None


def register(slug: str, conf_path: str = CONF_PATH, *, trusted_uid: int = 0) -> str:
    """Append the entry. Returns 'registered' or 'already registered'."""
    if SLUG.fullmatch(slug) is None:
        raise Refused("slug must use lowercase letters, numbers and single hyphens")
    conf = read_conf(conf_path, trusted_uid)
    registry = conf["REGISTRY"]
    data_dir = _workspace(slug, conf["DATA_ROOT"], conf["SERVICE_USER"])
    lock_path = os.path.join(
        os.path.dirname(registry), f".{os.path.basename(registry)}.onboarding.lock"
    )
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with open(registry, encoding="utf-8") as handle:
                old = handle.read()
            mode_source = os.stat(registry)
        except FileNotFoundError as exc:
            raise Refused(
                f"no registry at {registry}; run wingman-provision-shared.sh first"
            ) from exc
        if _existing(old, slug, data_dir) == "same":
            return "already registered"
        addition = (
            ("" if not old or old.endswith("\n\n") else "\n")
            + "[[tenant]]\n"
            + f"slug = {json.dumps(slug)}\n"
            + f"data_dir = {json.dumps(data_dir)}\n"
        )
        handle_fd, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(registry)}.", dir=os.path.dirname(registry)
        )
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                handle.write(old + addition)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, stat.S_IMODE(mode_source.st_mode))
            os.chown(temporary, mode_source.st_uid, mode_source.st_gid)
            os.replace(temporary, registry)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return "registered"
    finally:
        os.close(fd)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: wingman-register-tenant <slug>", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("must run as root (through its sudoers rule)", file=sys.stderr)
        return 2
    try:
        print(f"{register(argv[1])}: {argv[1]}")
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"refused: {exc.strerror or exc}; nothing was changed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
