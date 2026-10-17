"""Host-configured wrappers around existing OAuth CLI operations (#614)."""

from __future__ import annotations

import argparse
import os
import pwd
import re
import shlex
import subprocess
import sys
from pathlib import Path


class OperatorError(Exception):
    """Safe operator-facing failure, with no source configuration contents."""


def safe(text: str, subjects: tuple[str, ...] = ()) -> str:
    for subject in subjects:
        text = text.replace(subject, "…" + subject[-4:])
    text = re.sub(r"subject=([^\s]+)", lambda m: "subject=…" + m[1][-4:], text)
    text = re.sub(r"\buser_(?!management\b)[A-Za-z0-9_-]+", lambda m: "…" + m[0][-4:], text)
    return re.sub(r"[^\s<>]+@[^\s<>]+", "[address withheld]", text)


def execute(argv: list[str], subjects: tuple[str, ...] = (), *, show: bool = True) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if show:
        print(safe(result.stdout, subjects), end="")
        print(safe(result.stderr, subjects), end="", file=sys.stderr)
    if result.returncode:
        raise OperatorError("Underlying tenant command failed. No later operation was attempted.")
    return result.stdout


def read_host(path: Path) -> str:
    try:
        return path.read_text()
    except PermissionError:
        # Capture only; file contents can include a client secret and must never be echoed.
        result = subprocess.run(
            ["sudo", "-n", "cat", str(path)], capture_output=True, text=True, check=False
        )
        if result.returncode:
            raise OperatorError(
                "Cannot read shared host configuration (needs sudo); nothing changed."
            )
        return result.stdout


def settings() -> tuple[str, Path, Path, Path, Path]:
    user = os.environ.get("WINGMAN_SHARED_USER", "wingman-shared")
    home_result = subprocess.run(
        ["getent", "passwd", user], capture_output=True, text=True, check=False
    )
    if home_result.returncode == 0 and home_result.stdout.strip():
        home = Path(home_result.stdout.strip().split(":")[5])
    else:
        try:
            home = Path(pwd.getpwnam(user).pw_dir)
        except KeyError:
            raise OperatorError("Shared service account not found; nothing changed.") from None
    etc = Path(os.environ.get("WINGMAN_HOST_ROOT", "/")) / "etc/wingman"
    pointer = etc / "oauth-config-path"
    try:
        config = Path(read_host(pointer).strip())
    except FileNotFoundError:
        config = etc / "oauth.env"
    values = {}
    for line in read_host(config).splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            value = value.strip()
            values[key] = (
                value[1:-1]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}
                else value
            )
    argv = shlex.split(values.get("WINGMAN_OAUTH_ARGS", ""))
    if "--oauth-identities" not in argv:
        raise OperatorError("Shared OAuth identity-map setting is missing; nothing changed.")
    identities = Path(argv[argv.index("--oauth-identities") + 1])
    registry = Path(os.environ.get("WINGMAN_TENANT_REGISTRY", str(etc / "tenants.toml")))
    return user, home, identities, registry, config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("invite", "invites", "revoke", "pending", "approve", "bind")
    )
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    if args.command == "invite" and not (
        (args.csv and not args.arguments) or (not args.csv and len(args.arguments) == 2)
    ):
        parser.error("invite needs <email> <slug> or --csv <file>")
    expected = {"invites": 0, "pending": 0, "revoke": 1, "approve": 2, "bind": 2}
    if args.command in expected and len(args.arguments) != expected[args.command]:
        parser.error(f"{args.command}: expected {expected[args.command]} arguments")
    try:
        # Authenticate with inherited terminal output before capturing any command's
        # output. Otherwise sudo's password prompt would be hidden in the pipe.
        authenticated = subprocess.run(["sudo", "-v"], check=False)
        if authenticated.returncode:
            raise OperatorError("sudo authentication failed; nothing changed.")
        user, home, identities, registry, config = settings()
        binary = str(home / ".local/bin/wingman")
        shared = ["sudo", "-n", "-H", "-u", user, binary]
        root = ["sudo", "-n", binary]
        common = ["--identities", str(identities)]
        binding = [
            *common,
            "--registry",
            str(registry),
            "--oauth-config",
            str(config),
            "--oauth-web-config",
            str(Path(os.environ.get("WINGMAN_HOST_ROOT", "/")) / "etc/wingman/oauth-web.env"),
        ]
        data_root = os.environ.get("WINGMAN_SHARED_DATA_ROOT", str(home / "tenants"))
        if args.command == "invite":
            operation = (
                ["--from-csv", str(args.csv)]
                if args.csv
                else ["--email", args.arguments[0], "--slug", args.arguments[1]]
            )
            execute(
                [
                    *root,
                    "tenant",
                    "oauth-invite",
                    *operation,
                    *common,
                    "--registry",
                    str(registry),
                    "--data-root",
                    data_root,
                    "--service-user",
                    user,
                ]
            )
        elif args.command in {"invites", "pending", "revoke"}:
            command = {
                "invites": "oauth-invites",
                "pending": "oauth-pending",
                "revoke": "oauth-invite-revoke",
            }[args.command]
            execute([*shared, "tenant", command, *args.arguments, *common])
        else:
            reference, slug = args.arguments
            output = execute([*shared, "tenant", "oauth-pending", *common], show=False)
            rows = []
            for line in output.splitlines():
                fields = dict(item.split("=", 1) for item in line.split() if "=" in item)
                if fields.get("ref") == reference:
                    rows.append(fields)
            if len(rows) != 1:
                raise OperatorError(
                    f"Reference matches {len(rows)} pending rows; expected exactly one. Nothing changed."
                )
            subject = rows[0]["subject"]
            identity = ["--issuer", rows[0]["issuer"], "--subject", subject]
            # Existing oauth-bind preflight checks all trusted issuers without provisioning.
            execute(
                [*shared, "tenant", "oauth-bind", slug, *identity, *binding, "--preflight"],
                (subject,),
            )
            print(
                f"{args.command} reference {reference} (subject …{subject[-4:]}) to workspace {slug}; all trusted issuers."
            )
            try:
                answer = input("Proceed? [y/N] ")
            except EOFError:
                answer = ""
            if answer.lower() != "y":
                print("Cancelled; no admission changes made.")
                return 0
            if args.command == "bind":
                execute([*shared, "tenant", "oauth-bind", slug, *identity, *binding], (subject,))
            else:
                execute(
                    [
                        *root,
                        "tenant",
                        "oauth-approve",
                        slug,
                        *identity,
                        *binding,
                        "--data-root",
                        data_root,
                        "--service-user",
                        user,
                    ],
                    (subject,),
                )
    except OperatorError as exc:
        print(str(exc))
        return 1
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(
            f"Operator wrapper could not complete ({type(exc).__name__}); check host configuration and permissions. No later operation was attempted."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
