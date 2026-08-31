"""Keychain-backed keys (RFC-019): env wins, keychain fills gaps, no secrets shown."""

import os
from pathlib import Path

import pytest

from wingman.infrastructure import keys as keys_module
from wingman.infrastructure.keys import (
    KNOWN_KEYS,
    KeyStoreError,
    ensure_env,
    get_key,
    key_status,
    set_key,
    unset_key,
)

_KNOWN_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "VOYAGE_API_KEY",
    "GITHUB_SHARED_ISSUES_KEY",
    "OPENROUTER_API_KEY",
)


@pytest.fixture(autouse=True)
def _restore_real_env() -> None:
    """'ensure_env' hydrates the REAL process environment by design (that's
    the point — a key becomes live with no subprocess restart), bypassing
    monkeypatch's own tracking. Without this, a test that hydrates a key
    here leaks it into every test that runs afterward, in this file or any
    other, until the process exits. 'store_workspace_key' deliberately does
    NOT hydrate env (RFC-048 — see test_store_workspace_key_never_mutates_env)
    but this fixture stays broad-safety-net-shaped regardless.
    """
    originals = {name: os.environ.get(name) for name in _KNOWN_ENV_VARS}
    yield
    for name, value in originals.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class FakeKeychain:
    """A dict-backed stand-in for the macOS 'security' CLI."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        verb = argv[1]
        service = argv[argv.index("-s") + 1]
        if verb == "add-generic-password":
            self.items[service] = argv[argv.index("-w") + 1]
            return 0, ""
        if verb == "find-generic-password":
            if service in self.items:
                return 0, self.items[service] + "\n"
            return 44, ""
        if verb == "delete-generic-password":
            return (0, "") if self.items.pop(service, None) is not None else (44, "")
        raise AssertionError(f"unexpected security verb {verb}")


@pytest.fixture
def chain(monkeypatch: pytest.MonkeyPatch) -> FakeKeychain:
    monkeypatch.setattr(keys_module, "keychain_available", lambda: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    return FakeKeychain()


def test_set_get_unset_roundtrip(chain: FakeKeychain) -> None:
    assert set_key("anthropic", "sk-ant-test", runner=chain) == "ANTHROPIC_API_KEY"
    assert chain.items == {"ANTHROPIC_API_KEY": "sk-ant-test"}
    assert get_key("anthropic", runner=chain) == "sk-ant-test"
    set_key("Anthropic", "sk-ant-replaced", runner=chain)  # case-insensitive, replaces
    assert get_key("anthropic", runner=chain) == "sk-ant-replaced"
    assert unset_key("anthropic", runner=chain)
    assert get_key("anthropic", runner=chain) is None
    assert not unset_key("anthropic", runner=chain)


def test_unknown_and_empty_keys_are_rejected(chain: FakeKeychain) -> None:
    with pytest.raises(KeyStoreError, match="known keys"):
        set_key("openai", "sk-whatever", runner=chain)
    with pytest.raises(KeyStoreError, match="empty"):
        set_key("anthropic", "   ", runner=chain)


def test_ensure_env_fills_gaps_but_never_overwrites(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    set_key("anthropic", "sk-from-keychain", runner=chain)
    set_key("voyage", "pa-from-keychain", runner=chain)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-shell")
    hydrated = ensure_env(runner=chain, home=tmp_path)  # no real ~/.config/keys.env in play
    assert hydrated == ["VOYAGE_API_KEY"]  # the gap
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-shell"  # env won
    assert os.environ["VOYAGE_API_KEY"] == "pa-from-keychain"


def test_ensure_env_without_keychain_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert ensure_env(home=tmp_path) == []
    with pytest.raises(KeyStoreError, match="macOS"):
        set_key("anthropic", "sk-ant-test")


def test_key_status_names_sources_not_values(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_key("voyage", "pa-secret-value", runner=chain)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    rows = key_status(runner=chain)
    assert ("anthropic", "ANTHROPIC_API_KEY", "environment") in rows
    assert ("voyage", "VOYAGE_API_KEY", "keychain") in rows
    assert not any("secret-value" in " ".join(row) for row in rows)


def test_test_key_reports_not_set_without_any_network_call(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_key: str) -> tuple[bool, str]:
        raise AssertionError("must not be called when the key is unset")

    monkeypatch.setattr(keys_module, "_test_anthropic", boom)
    monkeypatch.setattr(keys_module, "_test_voyage", boom)
    assert keys_module.test_key("anthropic", runner=chain) == (False, "not set")


def test_test_key_reports_provider_result(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-whatever")
    seen: list[str] = []

    def fake_anthropic(api_key: str) -> tuple[bool, str]:
        seen.append(api_key)
        return True, "working"

    monkeypatch.setattr(keys_module, "_test_anthropic", fake_anthropic)
    assert keys_module.test_key("anthropic", runner=chain) == (True, "working")
    assert seen == ["sk-whatever"]  # the real value reached the tester, never logged


def test_test_key_surfaces_rejection(chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-bad")
    monkeypatch.setattr(
        keys_module, "_test_voyage", lambda _key: (False, "Voyage API returned 401 for...")
    )
    worked, message = keys_module.test_key("voyage", runner=chain)
    assert not worked
    assert "401" in message


def test_test_keys_covers_every_known_key(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keys_module, "_test_anthropic", lambda _key: (True, "working"))
    monkeypatch.setattr(keys_module, "_test_voyage", lambda _key: (True, "working"))
    monkeypatch.setattr(keys_module, "_test_github", lambda _key: (True, "working"))
    monkeypatch.setattr(keys_module, "_test_openrouter", lambda _key: (True, "working"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-x")
    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "ghp-x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    rows = keys_module.test_keys(runner=chain)
    assert {row[0] for row in rows} == set(KNOWN_KEYS)
    assert all(row[2] for row in rows)  # all worked


def test_test_key_github_reports_provider_result(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "ghp-whatever")
    seen: list[str] = []

    def fake_github(api_key: str) -> tuple[bool, str]:
        seen.append(api_key)
        return True, "working"

    monkeypatch.setattr(keys_module, "_test_github", fake_github)
    assert keys_module.test_key("github", runner=chain) == (True, "working")
    assert seen == ["ghp-whatever"]


def test_test_key_openrouter_reports_provider_result(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-whatever")
    seen: list[str] = []

    def fake_openrouter(api_key: str) -> tuple[bool, str]:
        seen.append(api_key)
        return True, "working"

    monkeypatch.setattr(keys_module, "_test_openrouter", fake_openrouter)
    assert keys_module.test_key("openrouter", runner=chain) == (True, "working")
    assert seen == ["sk-or-whatever"]


def test_workspace_key_file_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC-034 as amended: the workspace's own key wins; env is a fallback."""
    from wingman.infrastructure.keys import (
        ensure_env,
        read_workspace_keys,
        store_workspace_key,
        workspace_keys_path,
    )

    home = tmp_path / "home"  # no real ~/.config/keys.env in play
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("wingman.infrastructure.keys.keychain_available", lambda: False)
    assert store_workspace_key(tmp_path, "anthropic", "sk-ant-stored") is True
    assert read_workspace_keys(tmp_path) == {"ANTHROPIC_API_KEY": "sk-ant-stored"}
    assert (workspace_keys_path(tmp_path).stat().st_mode & 0o777) == 0o600

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    hydrated = ensure_env(data_dir=tmp_path, home=home)
    assert "ANTHROPIC_API_KEY" in hydrated
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-stored"

    # Hydration still never overwrites a set variable — but the stored key
    # is now the one a provider uses, so storing reports live, not shadowed.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert ensure_env(data_dir=tmp_path, home=home) == []
    assert store_workspace_key(tmp_path, "anthropic", "sk-ant-newer") is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-env"  # env untouched
    from wingman.infrastructure.keys import resolve_provider_key

    assert resolve_provider_key("ANTHROPIC_API_KEY", tmp_path) == "sk-ant-newer"


def test_store_workspace_key_never_mutates_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-048: under a shared multi-tenant process, mutating os.environ here
    would make one tenant's just-submitted key silently become every other
    tenant's key. 'store_workspace_key' must only write the workspace file —
    'resolve_provider_key' is the read path that picks it up instead."""
    from wingman.infrastructure.keys import store_workspace_key

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert store_workspace_key(tmp_path, "anthropic", "sk-ant-stored") is True
    assert store_workspace_key(tmp_path, "voyage", "pa-stored") is True
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "VOYAGE_API_KEY" not in os.environ


def test_resolve_provider_key_reads_workspace_file_without_mutating_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.keys import resolve_provider_key, store_workspace_key

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store_workspace_key(tmp_path, "anthropic", "sk-ant-stored")
    assert resolve_provider_key("ANTHROPIC_API_KEY", tmp_path) == "sk-ant-stored"
    assert "ANTHROPIC_API_KEY" not in os.environ  # read-only, still


def test_the_workspaces_own_key_beats_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BYOK: the key you provided is the key that gets used.

    Inverts RFC-019/RFC-034's env-always-wins. That order was right when
    one person ran one workspace on their own machine and wrong the moment
    a shared process serves several: an operator's stale export silently
    spending on a tenant's behalf is an attribution problem, and a form
    that stores a key it will not use is a trap.
    """
    from wingman.infrastructure.keys import resolve_provider_key, store_workspace_key

    store_workspace_key(tmp_path, "anthropic", "sk-ant-workspace")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert resolve_provider_key("ANTHROPIC_API_KEY", tmp_path) == "sk-ant-workspace"


def test_the_environment_is_still_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inverting precedence must not remove the env as a source — CI,
    containers and a fresh workspace all depend on it."""
    from wingman.infrastructure.keys import resolve_provider_key

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert resolve_provider_key("ANTHROPIC_API_KEY", tmp_path) == "sk-ant-env"


def test_resolve_provider_key_none_when_unset_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.keys import resolve_provider_key

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve_provider_key("ANTHROPIC_API_KEY", tmp_path) is None
    assert resolve_provider_key("ANTHROPIC_API_KEY", None) is None


def test_host_key_file_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC-046: the host secrets file sits between the Keychain and the workspace file."""
    from wingman.infrastructure.keys import host_keys_path, read_host_keys

    home = tmp_path / "home"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    assert read_host_keys(home) == {}

    host_file = host_keys_path(home)
    host_file.parent.mkdir(parents=True)
    host_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-host\n", encoding="utf-8")
    assert read_host_keys(home) == {"ANTHROPIC_API_KEY": "sk-ant-from-host"}

    hydrated = ensure_env(home=home)
    assert hydrated == ["ANTHROPIC_API_KEY"]
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-host"


def test_host_key_file_lives_under_config_wingman(tmp_path: Path) -> None:
    """RFC-046: 'secrets.env' under '~/.config/wingman/', not the old flat
    '~/.config/keys.env' (RFC-040) — the split this PR migrates off of."""
    from wingman.infrastructure.keys import host_keys_path

    home = tmp_path / "home"
    assert host_keys_path(home) == home / ".config" / "wingman" / "secrets.env"


def test_host_file_outranks_workspace_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ladder order: environment > keychain > host file > workspace file."""
    from wingman.infrastructure.keys import host_keys_path, store_workspace_key

    home = tmp_path / "home"
    data_dir = tmp_path / "workspace"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    store_workspace_key(data_dir, "anthropic", "sk-ant-workspace")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # belt-and-suspenders; nothing to undo

    host_file = host_keys_path(home)
    host_file.parent.mkdir(parents=True)
    host_file.write_text("ANTHROPIC_API_KEY=sk-ant-host\n", encoding="utf-8")

    import os

    ensure_env(data_dir=data_dir, home=home)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-host"  # host file beats workspace file


def test_resolve_key_sources_names_the_winner(
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from wingman.infrastructure.keys import resolve_key_sources

    set_key("voyage", "pa-from-keychain", runner=chain)
    sources = resolve_key_sources({}, data_dir=tmp_path, home=tmp_path / "home", runner=chain)
    by_name = {source.short_name: source for source in sources}
    assert by_name["voyage"].winning_source == "keychain"
    assert by_name["voyage"].shadowed_by == []
    assert by_name["anthropic"].winning_source == "not set"


def test_resolve_key_sources_flags_a_conflicting_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact #122 scenario: the same key defined in two places with
    different values — the resolution ladder still picks one deterministically,
    but the operator needs to know the other copy is stale, not silently
    ignored."""
    from wingman.infrastructure.keys import resolve_key_sources, store_workspace_key

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    store_workspace_key(tmp_path, "anthropic", "sk-ant-workspace-copy")
    environ = {"ANTHROPIC_API_KEY": "sk-ant-shell-export"}
    sources = resolve_key_sources(environ, data_dir=tmp_path, home=tmp_path / "home")
    by_name = {source.short_name: source for source in sources}
    # BYOK: the workspace's own key wins, and the stale shell export is
    # what gets reported as shadowed. The #122 requirement is unchanged —
    # the operator must be told the other copy exists — only which one
    # wins has moved.
    assert by_name["anthropic"].winning_source == "workspace file"
    assert by_name["anthropic"].shadowed_by == ["environment"]


def test_global_keys_file_fills_gaps_below_the_host_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #205: a credential meant to be shared box-wide, not per-account
    — the global file fills what neither the environment nor the
    account's own host file set, but never overrides either."""
    from wingman.infrastructure.keys import ensure_env, read_global_keys

    home = tmp_path / "home"
    global_file = tmp_path / "etc" / "global-secrets.env"
    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    monkeypatch.delenv("GITHUB_SHARED_ISSUES_KEY", raising=False)

    assert read_global_keys(global_file) == {}

    global_file.parent.mkdir(parents=True)
    global_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-shared\n", encoding="utf-8")
    assert read_global_keys(global_file) == {"GITHUB_SHARED_ISSUES_KEY": "ghp-shared"}

    hydrated = ensure_env(home=home, global_path=global_file)
    assert "GITHUB_SHARED_ISSUES_KEY" in hydrated
    assert os.environ["GITHUB_SHARED_ISSUES_KEY"] == "ghp-shared"


def test_unreadable_file_is_treated_as_empty_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tier this account can't stat/read (e.g. not in the 'wingman' group
    for RFC-047's global file) must degrade to 'nothing here', not raise —
    Python 3.12 propagates PermissionError out of 'Path.exists()' where
    older Python swallowed it, and this crashed every 'wingman' command
    for an account lacking access to the global tier (live, on lobster,
    migrating dhk's own account for RFC-048 Phase 3)."""
    from wingman.infrastructure.keys import read_global_keys

    global_file = tmp_path / "global-secrets.env"
    global_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-shared\n", encoding="utf-8")

    real_exists = Path.exists

    def _denied_exists(self: Path) -> bool:
        if self == global_file:
            raise PermissionError(13, "Permission denied", str(global_file))
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _denied_exists)
    assert read_global_keys(global_file) == {}


def test_host_file_outranks_global_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An account's own secrets.env always overrides the shared default —
    never the other way around."""
    from wingman.infrastructure.keys import ensure_env, host_keys_path

    home = tmp_path / "home"
    global_file = tmp_path / "etc" / "global-secrets.env"
    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    monkeypatch.delenv("GITHUB_SHARED_ISSUES_KEY", raising=False)

    global_file.parent.mkdir(parents=True)
    global_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-global\n", encoding="utf-8")

    host_file = host_keys_path(home)
    host_file.parent.mkdir(parents=True)
    host_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-per-account\n", encoding="utf-8")

    ensure_env(home=home, global_path=global_file)
    assert os.environ["GITHUB_SHARED_ISSUES_KEY"] == "ghp-per-account"


def test_resolve_key_sources_names_the_global_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.keys import resolve_key_sources

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    global_file = tmp_path / "etc" / "global-secrets.env"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-shared\n", encoding="utf-8")

    sources = resolve_key_sources(
        {}, data_dir=tmp_path / "ws", home=tmp_path / "home", global_path=global_file
    )
    by_name = {source.short_name: source for source in sources}
    assert by_name["github"].winning_source == "global file (/etc/wingman/global-secrets.env)"
    assert by_name["github"].shadowed_by == []


def test_resolve_key_sources_no_warning_when_duplicate_values_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.keys import resolve_key_sources, store_workspace_key

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    store_workspace_key(tmp_path, "anthropic", "sk-ant-same")
    environ = {"ANTHROPIC_API_KEY": "sk-ant-same"}
    sources = resolve_key_sources(environ, data_dir=tmp_path, home=tmp_path / "home")
    by_name = {source.short_name: source for source in sources}
    assert by_name["anthropic"].shadowed_by == []


def test_fingerprint_shows_the_prefix_but_never_the_key() -> None:
    """The whole point of showing a key at all: enough to recognise it,
    not enough to use it."""
    from wingman.infrastructure.keys import PREFIX_CHARS, fingerprint

    secret = "sk-ant-api03-SECRET-TAIL-NOBODY-SHOULD-SEE"
    shown = fingerprint(secret)
    assert shown.startswith(secret[:PREFIX_CHARS])
    assert "SECRET-TAIL" not in shown
    assert "NOBODY-SHOULD-SEE" not in shown
    assert f"len {len(secret)}" in shown
    assert fingerprint("   ") == "(empty)"


def test_fingerprint_matches_for_equal_keys_and_differs_for_drifted_ones() -> None:
    """Comparing tiers is the job; the digest is what makes it possible
    without ever printing a value."""
    from wingman.infrastructure.keys import fingerprint

    assert fingerprint("sk-ant-same-key") == fingerprint("sk-ant-same-key")
    assert fingerprint("sk-ant-same-key") != fingerprint("sk-ant-other-key")


def test_describe_key_locations_marks_one_winner_and_keeps_the_losers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expired-key scenario: two tiers hold different values. The used
    one is flagged, and the stale one is still listed — hiding it is how a
    dead key stays hidden."""
    from wingman.infrastructure.keys import describe_key_locations, store_workspace_key

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    store_workspace_key(tmp_path, "anthropic", "sk-ant-the-live-one")
    environ = {"ANTHROPIC_API_KEY": "sk-ant-the-expired-one"}

    rows = describe_key_locations(environ, data_dir=tmp_path, home=tmp_path / "home")["anthropic"]
    winners = [row for row in rows if row.winner]
    assert len(winners) == 1
    assert winners[0].tier == "workspace file"

    present = {row.tier: row for row in rows if row.present}
    assert set(present) == {"workspace file", "environment"}
    assert present["environment"].winner is False
    assert present["environment"].fingerprint != winners[0].fingerprint
    assert all("the-live-one" not in (row.fingerprint or "") for row in rows)


def test_describe_key_locations_reports_a_key_that_is_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.keys import describe_key_locations

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    rows = describe_key_locations({}, data_dir=tmp_path, home=tmp_path / "home")["openrouter"]
    assert not any(row.present for row in rows)
    assert not any(row.winner for row in rows)


def test_describe_key_locations_names_the_file_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Where is it?' has to be answerable with a path you can go open."""
    from wingman.infrastructure.keys import describe_key_locations

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    rows = describe_key_locations({}, data_dir=tmp_path, home=tmp_path / "home")["anthropic"]
    by_tier = {row.tier: row for row in rows}
    assert by_tier["workspace file"].path == str(tmp_path / "keys.env")
    assert by_tier["environment"].path is None
    assert "secrets.env" in (by_tier["host file (~/.config/wingman/secrets.env)"].path or "")


def test_store_host_key_writes_0600_and_preserves_other_keys(tmp_path: Path) -> None:
    """Fixing one expired key must not drop the others sharing the file."""
    from wingman.infrastructure.keys import read_host_keys, store_host_key

    store_host_key("anthropic", "sk-ant-first", home=tmp_path)
    path = store_host_key("voyage", "pa-second", home=tmp_path)

    values = read_host_keys(tmp_path)
    assert values["ANTHROPIC_API_KEY"] == "sk-ant-first"
    assert values["VOYAGE_API_KEY"] == "pa-second"
    assert path.stat().st_mode & 0o777 == 0o600


def test_store_host_key_replaces_rather_than_appends(tmp_path: Path) -> None:
    from wingman.infrastructure.keys import read_host_keys, store_host_key

    store_host_key("anthropic", "sk-ant-expired", home=tmp_path)
    store_host_key("anthropic", "sk-ant-renewed", home=tmp_path)
    assert read_host_keys(tmp_path)["ANTHROPIC_API_KEY"] == "sk-ant-renewed"


def test_store_host_key_refuses_an_empty_value(tmp_path: Path) -> None:
    from wingman.infrastructure.keys import store_host_key

    with pytest.raises(KeyStoreError):
        store_host_key("anthropic", "   ", home=tmp_path)


def test_validate_keys_tests_the_winning_tier_not_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug 'keys test' has: a healthy key in env, an expired one in the
    workspace file that outranks it. Validation must test the one that is
    actually spent, and say where it came from."""
    from wingman.infrastructure import keys as module
    from wingman.infrastructure.keys import store_workspace_key, validate_keys

    monkeypatch.setattr(module, "keychain_available", lambda: False)
    store_workspace_key(tmp_path, "anthropic", "sk-ant-expired")
    tested: list[str] = []

    def fake_test(value: str) -> tuple[bool, str]:
        tested.append(value)
        return (False, "Anthropic rejected the key (invalid or revoked)")

    monkeypatch.setitem(module._LIVE_TESTS, "anthropic", fake_test)

    rows = validate_keys(
        {"ANTHROPIC_API_KEY": "sk-ant-healthy"},
        data_dir=tmp_path,
        home=tmp_path / "home",
        global_path=tmp_path / "none.env",
    )
    row = next(r for r in rows if r.short_name == "anthropic")
    assert tested == ["sk-ant-expired"]
    assert row.tier == "workspace file"
    assert row.ok is False
    assert "rejected" in row.message


def test_validate_keys_makes_no_call_for_an_unset_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import keys as module
    from wingman.infrastructure.keys import validate_keys

    monkeypatch.setattr(module, "keychain_available", lambda: False)

    def explode(value: str) -> tuple[bool, str]:
        raise AssertionError("no network call should happen for an unset key")

    for name in module.KNOWN_KEYS:
        monkeypatch.setitem(module._LIVE_TESTS, name, explode)

    rows = validate_keys(
        {}, data_dir=tmp_path, home=tmp_path / "home", global_path=tmp_path / "none.env"
    )
    assert all(row.tier == "not set" and row.message == "not set" for row in rows)
    assert all(row.fingerprint is None for row in rows)


def test_validate_keys_never_returns_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure import keys as module
    from wingman.infrastructure.keys import validate_keys

    monkeypatch.setattr(module, "keychain_available", lambda: False)
    monkeypatch.setitem(module._LIVE_TESTS, "voyage", lambda value: (True, "working"))

    rows = validate_keys(
        {"VOYAGE_API_KEY": "pa-SECRET-TAIL-VALUE"},
        data_dir=tmp_path,
        home=tmp_path / "home",
        global_path=tmp_path / "none.env",
    )
    row = next(r for r in rows if r.short_name == "voyage")
    assert row.ok is True
    assert "SECRET-TAIL-VALUE" not in (row.fingerprint or "")
    assert "SECRET-TAIL-VALUE" not in row.message


def test_test_key_value_refuses_an_empty_value_without_calling_out() -> None:
    from wingman.infrastructure.keys import test_key_value

    assert test_key_value("anthropic", "  ") == (False, "not set")


#: chmod(0) denies nobody when you are root, so the permission tests would
#: silently invert rather than fail loudly. Skip instead of pretending.
_needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root can read a 0o000 file; the denial cannot be staged"
)


def _unreadable(path: Path) -> Path:
    """A file that exists but this account cannot read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ANTHROPIC_API_KEY=sk-ant-hidden\n", encoding="utf-8")
    path.chmod(0o000)
    return path


@_needs_non_root
def test_unreadable_tier_is_reported_as_unreadable_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#442: 'not set' when we could not look is a confident wrong answer —
    it sends someone off to set a key that is already there."""
    from wingman.infrastructure.keys import describe_key_locations

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    denied = _unreadable(tmp_path / "global-secrets.env")
    try:
        rows = describe_key_locations(
            {}, data_dir=tmp_path / "ws", home=tmp_path / "home", global_path=denied
        )["anthropic"]
    finally:
        denied.chmod(0o600)
    global_row = next(r for r in rows if "global" in r.tier)
    assert global_row.readable is False
    assert global_row.present is False
    # A tier we simply do not have is still plainly absent, not "unreadable".
    assert all(r.readable for r in rows if "global" not in r.tier)


@_needs_non_root
def test_validate_will_not_call_a_key_missing_when_a_tier_was_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.keys import validate_keys

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    denied = _unreadable(tmp_path / "global-secrets.env")
    try:
        rows = validate_keys(
            {}, data_dir=tmp_path / "ws", home=tmp_path / "home", global_path=denied
        )
    finally:
        denied.chmod(0o600)
    row = next(r for r in rows if r.short_name == "anthropic")
    assert row.ok is False
    assert row.message != "not set"
    assert "cannot tell" in row.message
    assert row.unreadable_tiers


@_needs_non_root
def test_ensure_env_still_tolerates_an_unreadable_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-047: one inaccessible tier must never crash every command for
    that account. #442 changed what the DIAGNOSTICS say, not this."""
    from wingman.infrastructure.keys import ensure_env

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    denied = _unreadable(tmp_path / "global-secrets.env")
    try:
        hydrated = ensure_env(data_dir=tmp_path / "ws", home=tmp_path / "home", global_path=denied)
    finally:
        denied.chmod(0o600)
    assert hydrated == []


def test_a_genuinely_absent_tier_is_still_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent and unreadable must stay distinguishable in both directions."""
    from wingman.infrastructure.keys import describe_key_locations

    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    rows = describe_key_locations(
        {}, data_dir=tmp_path / "ws", home=tmp_path / "home", global_path=tmp_path / "nope.env"
    )["anthropic"]
    assert all(row.readable for row in rows)
    assert not any(row.present for row in rows)
