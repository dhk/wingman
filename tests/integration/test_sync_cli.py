"""'wingman sync' is the one-command maintenance loop with visible degradation."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import wingman.application.people as people_module
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.fetch import FetchError

runner = CliRunner()

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>{title}</title>
    <link>https://example.substack.com/p/{slug}</link>
    <content:encoded><![CDATA[<p>{body}</p>]]></content:encoded>
  </item></channel></rss>
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    runner.invoke(app, ["init"])
    config = load_config()
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )

    def fake_fetch(url: str) -> bytes:
        slug = url.removeprefix("https://").split(".")[0]
        return FEED.format(title=f"{slug} post", slug=slug, body=f"{slug} on kafka").encode()

    monkeypatch.setattr(people_module, "fetch_url", fake_fetch)
    for name, slug in (("Jane Author", "jane"), ("Sam Writer", "sam")):
        runner.invoke(app, ["people", "add", name, "--substack", f"https://{slug}.substack.com"])
    return data_dir


def test_sync_fetches_and_embeds(workspace: Path) -> None:
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "Fetched 2/2 sources  new posts: 2" in result.output
    assert "Embedded 2 new documents (hashed/hashed-256)" in result.output
    assert "Sync complete." in result.output

    again = runner.invoke(app, ["sync"])
    assert again.exit_code == 0
    assert "new posts: 0" in again.output
    assert "Embedded 0 new documents" in again.output


def test_sync_tolerates_per_person_failures(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy = people_module.fetch_url

    def flaky(url: str) -> bytes:
        if "jane" in url:
            raise FetchError("connection refused")
        return healthy(url)

    monkeypatch.setattr(people_module, "fetch_url", flaky)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "Jane Author: fetch failed" in result.output
    assert "Fetched 1/2 sources" in result.output


def test_sync_fails_visibly_when_embedding_unconfigured(
    workspace: Path,
) -> None:
    config = load_config()
    # voyage without a key: fetch succeeds, embed step fails loud, posts kept
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "voyage"\nmodel = "voyage-4"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "new posts: 2" in result.output
    assert "Embedding skipped" in result.output
    assert "VOYAGE_API_KEY" in result.output

    status = runner.invoke(app, ["status"])
    assert "External documents: 2" in status.output


def test_sync_with_nothing_configured(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = workspace.parent / "empty-ws"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "nothing to sync" in result.output
