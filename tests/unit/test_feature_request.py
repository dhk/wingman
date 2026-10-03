"""Feature requests (RFC-025): the gate is structural, the write uses the user's gh."""

from pathlib import Path

import pytest

from wingman.application.feature_request import (
    _default_runner,
    file_feature_request,
    get_feature_repo,
    render_preview,
    set_feature_repo,
    stamp_operator,
)
from wingman.application.ingest import IngestError
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return tmp_path


def test_repo_roundtrip_and_validation(workspace: Path) -> None:
    config = load_config()
    assert get_feature_repo(config) is None
    assert set_feature_repo(config, " dhk/wingman ") == "dhk/wingman"
    assert get_feature_repo(config) == "dhk/wingman"
    for bad in ("wingman", "a/b/c", "/wingman", "dhk/"):
        with pytest.raises(IngestError, match="owner/name"):
            set_feature_repo(config, bad)


def test_preview_shows_exactly_what_would_be_filed(workspace: Path) -> None:
    preview = render_preview("dhk/wingman", "Add X", "Because Y.")
    assert "Repo:  dhk/wingman" in preview
    assert "Title: Add X" in preview
    assert "feature-request" in preview and "Because Y." in preview
    unset = render_preview(None, "Add X", "")
    assert "not set" in unset and "(empty)" in unset


def test_filing_requires_repo_and_title(workspace: Path) -> None:
    config = load_config()
    with pytest.raises(IngestError, match="needs a title"):
        file_feature_request(config, "   ", "body")
    with pytest.raises(IngestError, match="feature repo"):
        file_feature_request(config, "Add X", "body")


def test_filing_calls_gh_with_the_exact_payload(workspace: Path) -> None:
    config = load_config()
    set_feature_repo(config, "dhk/wingman")
    calls: list[list[str]] = []

    def fake_gh(argv: list[str]) -> tuple[int, str, str]:
        calls.append(argv)
        return 0, "https://github.com/dhk/wingman/issues/99\n", ""

    filed = file_feature_request(config, "Add  X", "Because Y.", runner=fake_gh)
    assert filed.url == "https://github.com/dhk/wingman/issues/99"
    [argv] = calls
    assert argv[:3] == ["gh", "issue", "create"]
    assert argv[argv.index("--repo") + 1] == "dhk/wingman"
    assert argv[argv.index("--title") + 1] == "Add X"  # whitespace normalized
    assert argv[argv.index("--body") + 1] == "Because Y."
    assert argv[argv.index("--label") + 1] == "feature-request"


def test_gh_failures_are_actionable(workspace: Path) -> None:
    config = load_config()
    set_feature_repo(config, "dhk/wingman")

    def failing(argv: list[str]) -> tuple[int, str, str]:
        return 1, "", "could not add label: 'feature-request' not found"

    with pytest.raises(IngestError, match="gh label create feature-request"):
        file_feature_request(config, "Add X", "b", runner=failing)

    def missing(argv: list[str]) -> tuple[int, str, str]:
        raise FileNotFoundError("gh")

    with pytest.raises(IngestError, match="gh auth login"):
        file_feature_request(config, "Add X", "b", runner=missing)


def test_stamp_operator_appends_submitted_by_when_set(tmp_path: Path) -> None:
    from wingman.infrastructure.host_config import wingman_env_path

    home = tmp_path / "home"
    path = wingman_env_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("WINGMAN_OPERATOR_NAME=Trent\n", encoding="utf-8")

    assert stamp_operator("Because Y.", home=home) == "Because Y.\n\nSubmitted by: Trent"
    assert stamp_operator("", home=home) == "Submitted by: Trent"


def test_stamp_operator_is_a_noop_when_unset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert stamp_operator("Because Y.", home=home) == "Because Y."
    assert stamp_operator("", home=home) == ""


def test_default_runner_translates_an_explicit_github_key_to_gh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_default_runner no longer resolves anything itself (that's
    _resolve_github_key's job, tested below) — it's a pure translator:
    given a key, set GH_TOKEN; given none, leave the environment alone."""
    captured: dict[str, object] = {}

    class FakeResult:
        returncode = 0
        stdout = "https://github.com/dhk/wingman/issues/1\n"
        stderr = ""

    def fake_run(argv: list[str], **kwargs: object) -> FakeResult:
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return FakeResult()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    _default_runner(["gh", "issue", "create"], github_key="ghp-explicit-token")

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghp-explicit-token"


def test_default_runner_leaves_gh_token_alone_when_no_key_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv: list[str], **kwargs: object) -> FakeResult:
        captured["env"] = kwargs.get("env")
        return FakeResult()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("GH_TOKEN", "already-authenticated-token")

    _default_runner(["gh", "issue", "create"])  # github_key defaults to None

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "already-authenticated-token"  # untouched, not overwritten


def test_resolve_github_key_prefers_a_tenant_config_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wingman.application.feature_request import _resolve_github_key
    from wingman.infrastructure.config import Config

    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "shared-process-env-value")
    config = Config(
        data_dir=Path("/nonexistent"),
        data_dir_source="test",
        github_shared_issues_key="tenants-own-key",
        strict_provider_keys=True,
    )
    assert _resolve_github_key(config) == "tenants-own-key"


def test_resolve_github_key_falls_back_to_env_outside_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wingman.application.feature_request import _resolve_github_key
    from wingman.infrastructure.config import Config

    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "shape-b-env-value")
    # Non-strict is now stated rather than inherited: since #532 a Config
    # built without an opinion is STRICT, so a test about the ambient
    # ladder has to ask for the ambient ladder.
    config = Config(
        data_dir=Path("/nonexistent"), data_dir_source="test", strict_provider_keys=False
    )
    assert _resolve_github_key(config) == "shape-b-env-value"


def test_a_config_built_without_an_opinion_does_not_inherit_ambient_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#532: the value a Config gets by not being thought about must be the
    one that costs a refusal, not the one that spends somebody else's key.
    'privileged' and 'funded' already worked this way; this one did not."""
    from wingman.application.feature_request import _resolve_github_key
    from wingman.infrastructure.config import Config

    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "somebody-elses-export")
    config = Config(data_dir=Path("/nonexistent"), data_dir_source="test")
    assert config.strict_provider_keys is True
    assert _resolve_github_key(config) is None


def _strict_config() -> Config:
    return Config(data_dir=Path("/nonexistent"), data_dir_source="test", strict_provider_keys=True)


def test_resolve_github_key_strict_mode_never_falls_back_to_ambient_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-048's guarantee, preserved through #506: a tenant must never
    inherit whatever the account running a shared process happened to
    export. Here the env var IS set and the operator's declared tiers are
    empty, so the answer is nothing at all."""
    from wingman.application.feature_request import _resolve_github_key

    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "shared-process-env-value")
    assert (
        _resolve_github_key(
            _strict_config(),
            home=tmp_path / "empty-home",
            global_path=tmp_path / "absent-global.env",
        )
        is None
    )


def test_resolve_github_key_strict_mode_uses_the_operators_declared_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#506: RFC-047's shared credential must actually REACH a tenant who
    has none of their own — that is the whole point of a box-wide PAT for
    accounts with no GitHub identity (#167). The ambient env is set to a
    different value so the assertion proves which source was read."""
    from wingman.application.feature_request import _resolve_github_key

    global_file = tmp_path / "etc" / "global-secrets.env"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-operator-provisioned\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "ambient-must-not-win")

    assert (
        _resolve_github_key(_strict_config(), home=tmp_path / "empty-home", global_path=global_file)
        == "ghp-operator-provisioned"
    )


def test_declared_host_file_outranks_the_global_file(tmp_path: Path) -> None:
    """RFC-047's ladder order: an account's own secrets.env overrides the
    box-wide default, never the reverse."""
    from wingman.application.feature_request import _resolve_github_key
    from wingman.infrastructure.keys import host_keys_path

    global_file = tmp_path / "etc" / "global-secrets.env"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-global\n", encoding="utf-8")
    home = tmp_path / "home"
    host_file = host_keys_path(home)
    host_file.parent.mkdir(parents=True)
    host_file.write_text("GITHUB_SHARED_ISSUES_KEY=ghp-per-account\n", encoding="utf-8")

    assert (
        _resolve_github_key(_strict_config(), home=home, global_path=global_file)
        == "ghp-per-account"
    )


def test_a_file_written_before_the_rename_still_resolves(tmp_path: Path) -> None:
    """#506 renamed the credential; a running box's /etc file still spells
    it the old way. A rename must not be an outage."""
    from wingman.application.feature_request import _resolve_github_key

    global_file = tmp_path / "etc" / "global-secrets.env"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("GITHUB_API_ISSUES_KEY=ghp-legacy-spelling\n", encoding="utf-8")

    assert (
        _resolve_github_key(_strict_config(), home=tmp_path / "empty-home", global_path=global_file)
        == "ghp-legacy-spelling"
    )


def test_the_canonical_spelling_wins_when_a_file_carries_both(tmp_path: Path) -> None:
    """A half-migrated file must not resolve to the stale value."""
    from wingman.application.feature_request import _resolve_github_key

    global_file = tmp_path / "etc" / "global-secrets.env"
    global_file.parent.mkdir(parents=True)
    global_file.write_text(
        "GITHUB_API_ISSUES_KEY=ghp-old\nGITHUB_SHARED_ISSUES_KEY=ghp-new\n", encoding="utf-8"
    )

    assert (
        _resolve_github_key(_strict_config(), home=tmp_path / "empty-home", global_path=global_file)
        == "ghp-new"
    )


def test_stamp_operator_prefers_the_tenants_name_over_the_box_wide_setting(
    tmp_path: Path,
) -> None:
    """#506: one shared PAT makes GitHub's 'opened by' say the operator for
    everybody, and WINGMAN_OPERATOR_NAME is one file per box — so without a
    per-tenant name every tenant's issue is indistinguishable."""
    config = Config(data_dir=Path("/nonexistent"), data_dir_source="test", operator_name="jason")
    assert stamp_operator("Because Y.", home=tmp_path, config=config).endswith(
        "Submitted by: jason"
    )


def test_filing_end_to_end_uses_the_tenants_own_key_not_the_shared_env(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual wiring, not just the two pieces in isolation: a tenant
    Config's own github_shared_issues_key reaches GH_TOKEN through
    file_feature_request's default path, and the shared process's own
    env value never leaks in alongside or instead of it."""
    config = load_config()
    set_feature_repo(config, "dhk/wingman")
    config.github_shared_issues_key = "tenants-own-key"
    config.strict_provider_keys = True

    captured: dict[str, object] = {}

    class FakeResult:
        returncode = 0
        stdout = "https://github.com/dhk/wingman/issues/1\n"
        stderr = ""

    def fake_run(argv: list[str], **kwargs: object) -> FakeResult:
        captured["env"] = kwargs.get("env")
        return FakeResult()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("GITHUB_SHARED_ISSUES_KEY", "shared-process-env-value")

    file_feature_request(config, "Add X", "Because Y.")

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "tenants-own-key"


def test_mcp_tool_stamps_operator_name_into_the_preview(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview must show exactly what gets filed — stamping happens
    once, before both the preview and the actual filing."""
    from wingman import mcp_server
    from wingman.application import feature_request as feature_request_module

    config = load_config()
    set_feature_repo(config, "dhk/wingman")
    monkeypatch.setattr(
        feature_request_module.host_config, "operator_name", lambda home=None: "Trent"
    )

    preview = mcp_server.feature_request("Add X", "Because Y.", confirmed=False)
    assert "Submitted by: Trent" in preview

    calls: list[list[str]] = []

    def fake_gh(argv: list[str], github_key: str | None = None) -> tuple[int, str, str]:
        calls.append(argv)
        return 0, "https://github.com/dhk/wingman/issues/99\n", ""

    monkeypatch.setattr(feature_request_module, "_default_runner", fake_gh)
    mcp_server.feature_request("Add X", "Because Y.", confirmed=True)
    [argv] = calls
    assert "Submitted by: Trent" in argv[argv.index("--body") + 1]


def test_mcp_tool_gates_on_confirmation(workspace: Path) -> None:
    from wingman import mcp_server

    config = load_config()
    set_feature_repo(config, "dhk/wingman")
    preview = mcp_server.feature_request("Add X", "Because Y.", confirmed=False)
    assert "Not filed" in preview and "Title: Add X" in preview
    assert "confirmed=true only after they explicitly approve" in preview
    assert mcp_server.feature_request("", "", confirmed=True).startswith(
        "feature_request needs a title"
    )
