"""Feature requests (RFC-025): the gate is structural, the write uses the user's gh."""

from pathlib import Path

import pytest

from wingman.application.feature_request import (
    file_feature_request,
    get_feature_repo,
    render_preview,
    set_feature_repo,
)
from wingman.application.ingest import IngestError
from wingman.infrastructure.config import load_config
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
