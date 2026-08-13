"""digest(as_html=True): the MCP tool points at the HTML twin instead of
reading it back. The twin itself is already written by every overnight run
(reporting/digest_html.py, wired in application/focus.py) — this tool call
only needs to say where it landed, not render anything itself."""

from pathlib import Path

import pytest

from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


def _write_digest(
    config: Config, markdown: str = "# Overnight digest\n", with_html: bool = True
) -> Path:
    digest_dir = config.reports_dir / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    md_path = digest_dir / "overnight-20260813T050000Z.md"
    md_path.write_text(markdown, encoding="utf-8")
    (digest_dir / "latest.md").write_text(markdown, encoding="utf-8")
    if with_html:
        md_path.with_suffix(".html").write_text(
            "<!doctype html>\n<h1>digest</h1>\n", encoding="utf-8"
        )
    return md_path


def test_digest_without_as_html_returns_the_markdown_unchanged(workspace: Config) -> None:
    from wingman.mcp_server import digest

    _write_digest(workspace, markdown="# Overnight digest — real content\n")
    assert digest() == "# Overnight digest — real content\n"


def test_digest_as_html_points_at_the_twin_rather_than_dumping_it_inline(workspace: Config) -> None:
    from wingman.mcp_server import digest

    md_path = _write_digest(workspace)
    result = digest(as_html=True)
    assert str(md_path.with_suffix(".html")) in result
    assert "<!doctype" not in result  # a path, not the file's contents


def test_digest_as_html_falls_back_gracefully_when_no_twin_was_written(workspace: Config) -> None:
    """Digests written before this feature shipped have no .html file —
    say so plainly rather than crashing or claiming one exists."""
    from wingman.mcp_server import digest

    _write_digest(workspace, with_html=False)
    result = digest(as_html=True)
    assert "No HTML twin" in result


def test_digest_as_html_with_no_digests_yet_matches_the_plain_message(workspace: Config) -> None:
    from wingman.mcp_server import digest

    assert digest(as_html=True) == digest()
    assert "No digests yet" in digest()
