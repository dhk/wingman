"""No bare existence check on a path this account may not be allowed to read.

`Path.exists()` and `Path.is_file()` RAISE on EACCES rather than returning
False. Every shared-config path in this codebase lives under a directory
another account owns — `/etc/wingman` is `750 root:wingman` — so a bare
check there turns a permission problem into a traceback, or, worse, into
"the file is absent", which is a different problem with a different fix.

This guard exists because prose did not work. `_read_broadcast_fields`
already carried a comment naming this exact scenario — "unreadable is the
expected case on a misconfigured box (mode 600, or an account not in the
wingman group)" — and the same bug was then written twice more, once above
it (`motd show`, #401) and once below it (`load_registry`, #411), by
somebody who had just read that comment. A present, correct registry got
reported to an operator as "no such file" and cost an hour.

Modelled on `test_every_install_path_in_the_repo_goes_through_this_script`,
which is the one rule in this repo that has actually stopped a mistake
being repeated — it failed a raw `uv tool install` within seconds of one
being written, months after it was added.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "wingman"

#: Names that denote a path under a directory this account may not own.
SHARED_PATHS = {
    "OPERATOR_MESSAGE_PATH",
    "OPERATOR_QUESTION_PATH",
    "DEFAULT_REGISTRY_PATH",
    "registry_path",
}

#: Modules that own or resolve those paths, where any bare check is suspect
#: regardless of the receiver's name.
SHARED_PATH_MODULES = {"broadcast.py", "tenants.py"}

BARE_CHECKS = {"exists", "is_file", "is_dir"}

#: What makes a check safe: an explicit diagnosis, or handling the error
#: the check itself can raise.
GUARDS = {"permission_problem"}
GUARD_EXCEPTIONS = {"OSError", "PermissionError"}


def _guarded(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in GUARDS:
                return True
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            names = [node.type] if not isinstance(node.type, ast.Tuple) else node.type.elts
            if any(getattr(n, "id", None) in GUARD_EXCEPTIONS for n in names):
                return True
    return False


def _receiver(node: ast.Call) -> str:
    value = getattr(node.func, "value", None)
    return getattr(value, "id", "") or getattr(value, "attr", "")


def _offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if _guarded(function):
            continue
        for node in ast.walk(function):
            if (
                not isinstance(node, ast.Call)
                or getattr(node.func, "attr", None) not in BARE_CHECKS
            ):
                continue
            receiver = _receiver(node)
            interesting = receiver in SHARED_PATHS or (
                path.name in SHARED_PATH_MODULES and receiver in {"path", "source", "self"}
            )
            if interesting:
                found.append(
                    f"{path.name}:{node.lineno} {function.name}() -> {receiver}.{node.func.attr}()"
                )
    return found


def test_no_shared_config_path_is_checked_without_a_guard() -> None:
    """The check must either diagnose the permission case or handle the
    error it can raise. 'Absent' and 'unreadable' are different problems
    with different fixes, and collapsing them sends an operator to fix the
    wrong one."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        offenders.extend(_offenders(path))

    assert offenders == [], (
        "bare existence check on a path this account may not be allowed to read:\n  "
        + "\n  ".join(offenders)
        + "\n\nPath.exists() raises on EACCES. Route it through "
        "broadcast.permission_problem(), or handle OSError — and keep 'absent' and "
        "'unreadable' distinct in whatever you report."
    )


def test_the_guard_actually_catches_the_shape_it_is_for(tmp_path: Path) -> None:
    """A guard that cannot fail is not a guard. This is the exact code that
    shipped in motd_show (#401) and load_registry (#411)."""
    offending = tmp_path / "broadcast.py"
    offending.write_text(
        "from pathlib import Path\n"
        "OPERATOR_MESSAGE_PATH = Path('/etc/wingman/motd.json')\n"
        "def show():\n"
        "    if not OPERATOR_MESSAGE_PATH.exists():\n"
        "        return 'no message set'\n"
        "    return 'a message'\n",
        encoding="utf-8",
    )

    assert _offenders(offending), "the guard failed to see the bug it exists for"


@pytest.mark.parametrize(
    "guard",
    [
        "    denied = permission_problem(OPERATOR_MESSAGE_PATH)\n",
        "    try:\n        pass\n    except OSError:\n        pass\n",
    ],
    ids=["diagnosis", "handled"],
)
def test_a_guarded_check_is_accepted(tmp_path: Path, guard: str) -> None:
    """Both ways of being correct must pass, or the rule teaches people to
    write the one the test happens to recognise."""
    guarded = tmp_path / "broadcast.py"
    guarded.write_text(
        "from pathlib import Path\n"
        "OPERATOR_MESSAGE_PATH = Path('/etc/wingman/motd.json')\n"
        "def show():\n"
        f"{guard}"
        "    if not OPERATOR_MESSAGE_PATH.exists():\n"
        "        return 'no message set'\n"
        "    return 'a message'\n",
        encoding="utf-8",
    )

    assert _offenders(guarded) == []
