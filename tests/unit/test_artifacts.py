"""Recorded artifact urls (#355's enabler).

Wingman never publishes an artifact — MCP runs one way, so a server has no
route back into the client. What it does is remember WHERE the client put
one, because updating a page in place needs its url and a conversation that
did not publish it has no other way to know one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.artifacts import (
    forget_artifact,
    published_artifact,
    remember_artifact,
    render_artifacts,
)
from wingman.application.ingest import IngestError
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage

runner = CliRunner()
URL = "https://claude.ai/code/artifact/a2979f3b-1fb2-4a34-8b76-421565dc29cc"


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Storage:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    with Storage(config.db_path) as opened:
        yield opened


def test_a_second_publish_replaces_rather_than_accumulates(storage: Storage) -> None:
    """One current page per kind. A list of former urls is a list of pages
    that are now wrong, and 'update my progress page' has to resolve without
    asking which one."""
    remember_artifact("completeness", URL, storage)
    remember_artifact("completeness", "https://claude.ai/code/artifact/second", storage)

    assert len(storage.list_published_artifacts()) == 1
    found = published_artifact("completeness", storage)
    assert found is not None and found.url.endswith("/second")


def test_the_url_survives_the_conversation_that_published_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: a LATER session — a scheduled one included — can find
    the page it must update. Without this it would publish a second one.

    So this closes the connection entirely and opens a new one, rather than
    reading back through the handle that wrote it.
    """
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    with Storage(config.db_path) as first:
        remember_artifact("values_radar", URL, first, title="Value profile")

    with Storage(config.db_path) as later:
        found = published_artifact("values_radar", later)

    assert found is not None
    assert found.url == URL
    assert found.title == "Value profile"


def test_an_unknown_kind_is_refused(storage: Storage) -> None:
    """The set is closed because each kind must be REGENERABLE for an update
    to mean anything — an artifact nothing can rebuild is a dead end."""
    with pytest.raises(IngestError, match="unknown artifact kind"):
        remember_artifact("horoscope", URL, storage)


def test_a_non_https_url_is_refused(storage: Storage) -> None:
    with pytest.raises(IngestError, match="must be https"):
        remember_artifact("profile", "http://example.com/x", storage)


def test_the_listing_never_implies_the_page_is_still_true(storage: Storage) -> None:
    """This table records where a page is, never whether it still reflects
    the workspace. A url with no date reads as current (#355)."""
    remember_artifact("completeness", URL, storage)

    rendered = render_artifacts(storage.list_published_artifacts())

    assert URL in rendered
    assert "published" in rendered
    assert "snapshots" in rendered


def test_forgetting_a_url_does_not_pretend_to_unpublish(storage: Storage) -> None:
    remember_artifact("profile", URL, storage)

    assert forget_artifact("profile", storage)
    assert published_artifact("profile", storage) is None
    assert not forget_artifact("profile", storage)


def test_the_cli_and_the_tool_reach_the_same_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-008 parity, and the reason it matters here: an operator recording
    a url from a terminal and an assistant reading it in a conversation must
    see the same page."""
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    Storage(config.db_path).close()

    assert runner.invoke(app, ["artifacts", "remember", "values_radar", URL]).exit_code == 0

    from wingman.mcp_server import artifacts as artifacts_tool

    assert URL in artifacts_tool(action="show", kind="values_radar")
    assert URL in runner.invoke(app, ["artifacts", "list"]).output
