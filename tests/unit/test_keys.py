"""Keychain-backed keys (RFC-019): env wins, keychain fills gaps, no secrets shown."""

import pytest

from wingman.infrastructure import keys as keys_module
from wingman.infrastructure.keys import (
    KeyStoreError,
    ensure_env,
    get_key,
    key_status,
    set_key,
    unset_key,
)


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
    chain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    set_key("anthropic", "sk-from-keychain", runner=chain)
    set_key("voyage", "pa-from-keychain", runner=chain)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-shell")
    hydrated = ensure_env(runner=chain)
    assert hydrated == ["VOYAGE_API_KEY"]  # the gap
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-shell"  # env won
    assert os.environ["VOYAGE_API_KEY"] == "pa-from-keychain"


def test_ensure_env_without_keychain_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys_module, "keychain_available", lambda: False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert ensure_env() == []
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
