"""The root-owned registration helper (RFC-086, #658).

It is the only code that runs as root on the approve-from-chat path, so these
pin the boundary: what it will not do matters more than what it does.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import pwd
import stat
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from wingman.infrastructure import register_helper
from wingman.infrastructure.oauth_onboarding import (
    _SLUG,
    OAuthOnboardingError,
    OAuthOnboardingStore,
    approve_pending_tenant,
    onboarding_path_for,
)
from wingman.infrastructure.tenants import load_registry

SCRIPT = Path(__file__).parents[2] / "scripts" / "wingman-register-tenant.py"
ISSUER = "https://idp.example.com"
ME = pwd.getpwuid(os.getuid()).pw_name


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("register_tenant_helper", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = _load()


class _Box:
    def __init__(self, tmp_path: Path) -> None:
        tmp_path = tmp_path.resolve()  # the helper refuses any link in the path
        self.registry = tmp_path / "tenants.toml"
        self.registry.write_text("# registry\n", encoding="utf-8")
        self.data_root = tmp_path / "tenants"
        self.data_root.mkdir()
        self.conf = tmp_path / "register-helper.conf"
        self.write_conf()

    def write_conf(self, **overrides: str) -> None:
        values = {
            "REGISTRY": str(self.registry),
            "DATA_ROOT": str(self.data_root),
            "SERVICE_USER": ME,
        }
        values.update(overrides)
        self.conf.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items() if value is not None),
            encoding="utf-8",
        )
        self.conf.chmod(0o644)

    def workspace(self, slug: str, *, db: bool = True) -> Path:
        data_dir = self.data_root / slug
        data_dir.mkdir()
        if db:
            (data_dir / "wingman.db").write_bytes(b"")
        return data_dir

    def register(self, slug: str) -> str:
        return helper.register(slug, str(self.conf), trusted_uid=os.getuid())


@pytest.fixture
def box(tmp_path: Path) -> _Box:
    return _Box(tmp_path)


def test_it_registers_one_unprivileged_unfunded_tenant(box: _Box) -> None:
    box.workspace("newcomer")
    assert box.register("newcomer") == "registered"
    (tenant,) = load_registry(box.registry)
    assert (tenant.slug, tenant.data_dir) == ("newcomer", box.data_root / "newcomer")
    assert not (tenant.privileged or tenant.funded or tenant.super_user)
    assert not tenant.capability_url_retired
    text = box.registry.read_text(encoding="utf-8")
    assert text.startswith("# registry\n")  # what was there is kept
    for flag in ("privileged", "funded", "super_user"):
        assert flag not in text


def test_registering_twice_changes_nothing_the_second_time(box: _Box) -> None:
    box.workspace("newcomer")
    box.register("newcomer")
    before = box.registry.read_bytes()
    assert box.register("newcomer") == "already registered"
    assert box.registry.read_bytes() == before


def test_registry_mode_survives_the_replacement(box: _Box) -> None:
    box.registry.chmod(0o640)
    box.workspace("newcomer")
    box.register("newcomer")
    assert stat.S_IMODE(box.registry.stat().st_mode) == 0o640


@pytest.mark.parametrize(
    "slug", ["", "A", "a b", "../etc", "a/b", "-a", "a-", "x;rm -rf /", "a" * 64, "ünï"]
)
def test_a_malformed_slug_is_refused_before_anything_is_read(box: _Box, slug: str) -> None:
    before = box.registry.read_bytes()
    with pytest.raises(helper.Refused, match="slug"):
        box.register(slug)
    assert box.registry.read_bytes() == before


def test_the_slug_pattern_is_the_one_wingman_uses() -> None:
    """The helper cannot import wingman, so the two copies are kept equal here."""
    assert helper.SLUG.pattern == _SLUG.pattern


def test_it_refuses_a_workspace_that_does_not_exist_or_is_empty(box: _Box) -> None:
    with pytest.raises(helper.Refused, match="does not exist"):
        box.register("ghost")
    box.workspace("hollow", db=False)
    with pytest.raises(helper.Refused, match="no wingman.db"):
        box.register("hollow")
    assert load_registry(box.registry) == []


def test_it_refuses_a_workspace_that_is_a_link(box: _Box, tmp_path: Path) -> None:
    target = tmp_path / "somewhere-else"
    target.mkdir()
    (target / "wingman.db").write_bytes(b"")
    (box.data_root / "linked").symlink_to(target)
    with pytest.raises(helper.Refused, match="plain directory"):
        box.register("linked")
    assert load_registry(box.registry) == []


def test_it_refuses_a_workspace_reached_through_a_linked_directory(
    box: _Box, tmp_path: Path
) -> None:
    """A link anywhere in the path, not just on the last part, could point the
    registered workspace somewhere the slug does not name."""
    box.workspace("newcomer")
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(box.data_root)
    box.write_conf(DATA_ROOT=str(alias))
    with pytest.raises(helper.Refused, match="plain directory"):
        box.register("newcomer")
    assert load_registry(box.registry) == []


def test_it_refuses_a_workspace_the_service_account_does_not_own(box: _Box) -> None:
    other = next(
        (u.pw_name for u in pwd.getpwall() if u.pw_uid != os.getuid() and u.pw_name != ME), None
    )
    if other is None:
        pytest.skip("no second account on this machine")
    box.workspace("newcomer")
    box.write_conf(SERVICE_USER=other)
    with pytest.raises(helper.Refused, match="not owned by"):
        box.register("newcomer")


def test_it_refuses_to_replace_an_existing_tenant_with_other_settings(
    box: _Box, tmp_path: Path
) -> None:
    box.workspace("newcomer")
    box.registry.write_text(
        f'[[tenant]]\nslug = "newcomer"\ndata_dir = "{tmp_path / "elsewhere"}"\n', encoding="utf-8"
    )
    before = box.registry.read_bytes()
    with pytest.raises(helper.Refused, match="already exists"):
        box.register("newcomer")
    assert box.registry.read_bytes() == before


def test_it_will_not_accept_an_existing_privileged_entry_as_already_registered(box: _Box) -> None:
    box.workspace("newcomer")
    box.registry.write_text(
        f'[[tenant]]\nslug = "newcomer"\ndata_dir = "{box.data_root / "newcomer"}"\n'
        "privileged = true\n",
        encoding="utf-8",
    )
    with pytest.raises(helper.Refused):
        box.register("newcomer")


def test_a_config_the_service_account_could_edit_is_refused(box: _Box) -> None:
    box.workspace("newcomer")
    box.conf.chmod(0o664)
    with pytest.raises(helper.Refused, match="owned by root"):
        box.register("newcomer")
    box.conf.chmod(0o644)
    with pytest.raises(helper.Refused, match="owned by root"):
        helper.register("newcomer", str(box.conf), trusted_uid=os.getuid() + 12345)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"REGISTRY": "relative/tenants.toml"}, "absolute"),
        ({"DATA_ROOT": "tenants"}, "absolute"),
        ({"SERVICE_USER": ""}, "missing"),
    ],
)
def test_a_malformed_config_is_refused(box: _Box, overrides: dict[str, str], message: str) -> None:
    box.workspace("newcomer")
    box.write_conf(**overrides)
    with pytest.raises(helper.Refused, match=message):
        box.register("newcomer")


def test_a_config_line_it_does_not_understand_is_refused(box: _Box) -> None:
    box.workspace("newcomer")
    box.conf.write_text(box.conf.read_text() + "EXTRA=1\n", encoding="utf-8")
    with pytest.raises(helper.Refused, match="does not understand"):
        box.register("newcomer")


def test_main_takes_exactly_one_argument_and_only_runs_as_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert helper.main(["x"]) == 2
    assert helper.main(["x", "a", "b"]) == 2
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert helper.main(["x", "newcomer"]) == 2
    assert "must run as root" in capsys.readouterr().err


def test_the_helper_imports_nothing_from_wingman_and_no_third_party() -> None:
    """Root must never execute code the service account can edit."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert "wingman" not in imports
    stdlib = {
        "__future__", "fcntl", "json", "os", "pwd", "re", "stat", "sys", "tempfile", "tomllib",
    }  # fmt: skip
    assert imports <= stdlib


def test_an_approval_through_the_helper_matches_the_in_process_writer(
    box: _Box, tmp_path: Path
) -> None:
    """The same approval, once with the helper's registration step: the
    registry entry is what the CLI's own writer would have produced."""
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    store = OAuthOnboardingStore(onboarding_path_for(identities))
    store.reserve_invite("newcomer", existing_slugs=set())
    store.record_pending(ISSUER, "user_01")

    data_dir = approve_pending_tenant(
        identity_path=identities,
        registry_path=box.registry,
        data_root=box.data_root,
        slug="newcomer",
        issuer=ISSUER,
        subject="user_01",
        service_user=ME,
        trusted_issuers=(ISSUER,),
        register=lambda _registry, slug, _dir: box.register(slug),
    )

    assert data_dir == box.data_root / "newcomer"
    (tenant,) = load_registry(box.registry)
    assert (tenant.slug, tenant.privileged, tenant.funded) == ("newcomer", False, False)
    assert store.invites() == [] and store.pending() == []


# --- the client side ----------------------------------------------------


class _Run:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode, self.stderr, self.stdout, self.calls = returncode, stderr, "", []

    def __call__(self, argv: list[str], **kwargs: object) -> _Run:
        self.calls.append((argv, kwargs))
        return self


@pytest.fixture
def client(box: _Box, monkeypatch: pytest.MonkeyPatch) -> _Box:
    monkeypatch.setattr(register_helper, "CONF_PATH", box.conf)
    return box


def test_the_client_passes_only_the_slug_and_never_uses_a_shell(
    client: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    register_helper.register_via_helper(client.registry, "newcomer", client.data_root / "newcomer")
    ((argv, kwargs),) = run.calls
    assert argv == ["sudo", "-n", str(register_helper.HELPER_PATH), "newcomer"]
    assert not kwargs.get("shell")


def test_the_client_surfaces_the_helper_refusal(
    client: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _Run(1, "line one\nrefused: no wingman.db\n"))
    with pytest.raises(OAuthOnboardingError, match="refused: no wingman.db"):
        register_helper.register_via_helper(
            client.registry, "newcomer", client.data_root / "newcomer"
        )


def test_the_client_refuses_a_registry_or_root_the_helper_is_not_set_for(
    client: _Box, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(OAuthOnboardingError, match="different registry"):
        register_helper.register_via_helper(
            tmp_path / "other.toml", "newcomer", client.data_root / "newcomer"
        )
    with pytest.raises(OAuthOnboardingError, match="different registry"):
        register_helper.register_via_helper(client.registry, "newcomer", tmp_path / "elsewhere")
    assert run.calls == []


def test_the_client_reports_a_helper_that_cannot_run(
    client: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("sudo", 30)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(OAuthOnboardingError, match="could not be run"):
        register_helper.register_via_helper(
            client.registry, "newcomer", client.data_root / "newcomer"
        )


def test_no_helper_installed_means_not_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(register_helper, "CONF_PATH", tmp_path / "missing.conf")
    assert register_helper.read_conf() is None
    assert register_helper.available() is False
