"""Where a feature request goes, resolved rather than asked (#371).

A hosted tenant has no terminal, so the per-workspace file written by
`wingman feature repo` is unreachable for them — every tenant preview read
"(not set)", which made wingman's one external write unusable for exactly
the people most likely to have a sharp request.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.application.feature_request import get_feature_repo, set_feature_repo
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant, TenantRegistryError, load_registry


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


def _registry(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_a_tenant_gets_a_destination_without_ever_choosing_one(tmp_path: Path) -> None:
    """The whole point. A tenant cannot run `wingman feature repo`, and must
    never be handed a repository chooser instead."""
    data_dir = tmp_path / "tenants" / "trent"
    data_dir.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    registry = _registry(
        tmp_path / "tenants.toml",
        f'[[tenant]]\nslug = "trent"\ndata_dir = "{data_dir}"\nfeature_repo = "dhk/wingman"\n',
    )

    tenant = load_registry(registry)[0]

    assert tenant.feature_repo == "dhk/wingman"
    assert get_feature_repo(tenant.config()) == "dhk/wingman"


def test_the_host_setting_covers_every_tenant_that_names_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The normal case: one destination for the whole box, set once."""
    data_dir = tmp_path / "tenants" / "jason"
    data_dir.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    config = Tenant(slug="jason", data_dir=data_dir).config()
    assert config.feature_repo is None

    monkeypatch.setattr(
        "wingman.infrastructure.host_config.read_host_settings",
        lambda home=None: {"WINGMAN_FEATURE_REPO": "dhk/wingman"},
    )

    assert get_feature_repo(config) == "dhk/wingman"


def test_an_explicit_local_choice_outranks_the_box_default(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody who ran `wingman feature repo` chose. A box-wide default is
    a fallback, not an override of a decision already taken."""
    config = load_config()
    set_feature_repo(config, "someone/their-own-repo")
    monkeypatch.setattr(
        "wingman.infrastructure.host_config.read_host_settings",
        lambda home=None: {"WINGMAN_FEATURE_REPO": "dhk/wingman"},
    )

    assert get_feature_repo(config) == "someone/their-own-repo"


def test_an_unreadable_host_file_reports_unset_rather_than_failing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same posture as the keys ladder: a malformed wingman.env should still
    let the tool say 'no repo set' instead of failing outright."""

    def boom(home: Path | None = None) -> dict[str, str]:
        raise OSError("permission denied")

    monkeypatch.setattr("wingman.infrastructure.host_config.read_host_settings", boom)

    assert get_feature_repo(load_config()) is None


def _tenant_dir(tmp_path: Path, slug: str) -> Path:
    data_dir = tmp_path / "tenants" / slug
    data_dir.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    return data_dir


def test_one_registry_line_gives_every_tenant_the_same_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The all-account default: the operator names the repo once, in the
    same file that lists the tenants, and nobody is asked anything."""
    jason = _tenant_dir(tmp_path, "jason")
    bob = _tenant_dir(tmp_path, "bob")
    registry = _registry(
        tmp_path / "tenants.toml",
        "[defaults]\n"
        'feature_repo = "dhk/wingman"\n\n'
        f'[[tenant]]\nslug = "jason"\ndata_dir = "{jason}"\n\n'
        f'[[tenant]]\nslug = "bob"\ndata_dir = "{bob}"\n',
    )
    # No host setting anywhere: the registry alone has to answer this.
    monkeypatch.setattr(
        "wingman.infrastructure.host_config.read_host_settings", lambda home=None: {}
    )

    tenants = load_registry(registry)

    assert [get_feature_repo(tenant.config()) for tenant in tenants] == [
        "dhk/wingman",
        "dhk/wingman",
    ]


def test_a_tenants_own_repo_outranks_the_all_account_default(tmp_path: Path) -> None:
    """The per-user line is the exception to the box-wide one, so it wins."""
    data_dir = _tenant_dir(tmp_path, "trent")
    registry = _registry(
        tmp_path / "tenants.toml",
        "[defaults]\n"
        'feature_repo = "dhk/wingman"\n\n'
        f'[[tenant]]\nslug = "trent"\ndata_dir = "{data_dir}"\n'
        'feature_repo = "dhk/adventures-in-ai"\n',
    )

    tenant = load_registry(registry)[0]

    assert get_feature_repo(tenant.config()) == "dhk/adventures-in-ai"


def test_an_explicit_local_choice_outranks_the_all_account_default(tmp_path: Path) -> None:
    """A migrated account keeps its `wingman feature repo` choice: the
    registry default is a default, not an override of a decision taken."""
    data_dir = _tenant_dir(tmp_path, "dhk")
    registry = _registry(
        tmp_path / "tenants.toml",
        "[defaults]\n"
        'feature_repo = "dhk/wingman"\n\n'
        f'[[tenant]]\nslug = "dhk"\ndata_dir = "{data_dir}"\n',
    )
    tenant = load_registry(registry)[0]
    set_feature_repo(tenant.config(), "someone/their-own-repo")

    assert get_feature_repo(tenant.config()) == "someone/their-own-repo"


def test_the_registry_default_outranks_the_host_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under a shared process the host settings file belongs to the service
    account, not to any tenant; the registry is where tenants are named, so
    what it says about them is the more specific statement."""
    data_dir = _tenant_dir(tmp_path, "barys")
    registry = _registry(
        tmp_path / "tenants.toml",
        "[defaults]\n"
        'feature_repo = "dhk/wingman"\n\n'
        f'[[tenant]]\nslug = "barys"\ndata_dir = "{data_dir}"\n',
    )
    monkeypatch.setattr(
        "wingman.infrastructure.host_config.read_host_settings",
        lambda home=None: {"WINGMAN_FEATURE_REPO": "someone/service-account-repo"},
    )

    assert get_feature_repo(load_registry(registry)[0].config()) == "dhk/wingman"


def test_a_bare_top_level_default_is_read_the_same_way(tmp_path: Path) -> None:
    """An operator will reasonably write the line without a table header
    when it sits above the tenants; TOML makes that the same key."""
    data_dir = _tenant_dir(tmp_path, "jason")
    registry = _registry(
        tmp_path / "tenants.toml",
        f'feature_repo = "dhk/wingman"\n\n[[tenant]]\nslug = "jason"\ndata_dir = "{data_dir}"\n',
    )

    assert load_registry(registry)[0].default_feature_repo == "dhk/wingman"


def test_two_disagreeing_defaults_are_refused_rather_than_ranked(tmp_path: Path) -> None:
    """Picking one silently would file somewhere the operator can't predict
    from reading their own file."""
    registry = _registry(
        tmp_path / "tenants.toml",
        'feature_repo = "dhk/one"\n[defaults]\nfeature_repo = "dhk/two"\n',
    )

    with pytest.raises(TenantRegistryError, match="two different default feature repos"):
        load_registry(registry)


def test_a_malformed_default_feature_repo_is_refused_loudly(tmp_path: Path) -> None:
    """Same posture as the per-tenant line: a typo names itself at load."""
    registry = _registry(
        tmp_path / "tenants.toml",
        '[defaults]\nfeature_repo = "not-a-repo"\n',
    )

    with pytest.raises(TenantRegistryError, match="malformed 'feature_repo'"):
        load_registry(registry)


def test_a_malformed_registry_feature_repo_is_refused_loudly(tmp_path: Path) -> None:
    """The registry is hand-edited by an operator; a typo there should name
    itself rather than silently filing somewhere unexpected."""
    registry = _registry(
        tmp_path / "tenants.toml",
        f'[[tenant]]\nslug = "t"\ndata_dir = "{tmp_path}"\nfeature_repo = "not-a-repo"\n',
    )

    with pytest.raises(TenantRegistryError, match="malformed 'feature_repo'"):
        load_registry(registry)
