"""'wingman demo' runs the whole tour on real pipelines with graceful degradation."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import wingman.application.people as people_module
from wingman.application.demo import DEMO_WATCHLIST
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR
from wingman.infrastructure.storage import Storage

runner = CliRunner()

FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <item>
      <title>{title}</title>
      <link>https://example.substack.com/p/post</link>
      <content:encoded><![CDATA[<p>{body}</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    def fake_fetch(url: str) -> bytes:
        slug = url.removeprefix("https://").split(".")[0]
        return FEED_TEMPLATE.format(
            title=f"{slug} on AI", body=f"{slug} writes about AI and data systems"
        ).encode("utf-8")

    monkeypatch.setattr(people_module, "fetch_url", fake_fetch)
    return data_dir


def test_demo_seeds_watchlist(workspace: Path) -> None:
    """All demo entries are valid and unique before anything network-y happens."""
    urls = [url for _, url in DEMO_WATCHLIST]
    assert all(url.startswith("https://") for url in urls)
    assert len(set(urls)) == len(urls)
    names = [" ".join(name.lower().split()) for name, _ in DEMO_WATCHLIST]
    assert len(set(names)) == len(names)


def test_demo_end_to_end_without_any_keys(workspace: Path) -> None:
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    assert f"{len(DEMO_WATCHLIST)} publications added" in result.output
    assert "new posts archived" in result.output
    # keyless degradation: similarity still runs, on the local provider
    assert "VOYAGE_API_KEY is not set" in result.output
    assert "hashed" in result.output
    assert "Closest to Dave Holmes-Kinsella:" in result.output
    assert "Demo complete" in result.output
    # everything lives in the isolated demo workspace, not the real one
    assert (workspace / "demo" / "wingman.db").is_file()
    assert not (workspace / "wingman.db").exists()
    with Storage(workspace / "demo" / "wingman.db") as storage:
        usage = storage.list_model_usage()
    assert any(row.provider == "hashed" and row.capability == "embed_semantic" for row in usage)


def test_demo_never_touches_the_real_workspace(workspace: Path) -> None:
    runner.invoke(app, ["init"])
    runner.invoke(
        app, ["people", "add", "My Person", "--substack", "https://myperson.substack.com"]
    )
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    # the real workspace still has exactly the one person, with nothing fetched
    status = runner.invoke(app, ["status"])
    assert "People: 1" in status.output
    assert "External documents: 0" in status.output
    # and the demo never fetched the real person's feed
    assert "My Person" not in result.output


def test_demo_is_idempotent(workspace: Path) -> None:
    assert runner.invoke(app, ["demo"]).exit_code == 0
    second = runner.invoke(app, ["demo"])
    assert second.exit_code == 0
    assert f"{len(DEMO_WATCHLIST)} already present" in second.output


def test_demo_survives_unreachable_feeds(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    healthy = people_module.fetch_url

    def flaky(url: str) -> bytes:
        calls["n"] += 1
        if "noahpinion" in url:
            from wingman.infrastructure.fetch import FetchError

            raise FetchError("connection refused")
        return healthy(url)

    monkeypatch.setattr(people_module, "fetch_url", flaky)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "feeds unreachable" in result.output


def test_demo_fails_visibly_when_nothing_fetchable(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.infrastructure.fetch import FetchError

    def down(url: str) -> bytes:
        raise FetchError("network unreachable")

    monkeypatch.setattr(people_module, "fetch_url", down)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 1
    assert "No feeds could be fetched" in result.output
