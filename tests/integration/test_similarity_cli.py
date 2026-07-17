"""The embed and similarity commands work end to end with the local provider."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import wingman.application.people as people_module
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR, load_config

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

BODIES = {
    "twin": "Kafka event streaming and billing pipelines",
    "bridge": "Kafka streaming with roses on the side",
    "garden": "Roses tulips compost and pruning",
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    runner.invoke(app, ["init"])
    config = load_config()
    # local hashed provider: no network, no key
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )

    def fake_fetch(url: str) -> bytes:
        slug = url.removeprefix("https://").split(".")[0]
        return FEED_TEMPLATE.format(title=f"{slug} post", body=BODIES[slug]).encode("utf-8")

    monkeypatch.setattr(people_module, "fetch_url", fake_fetch)
    for name, slug in (("Kafka Twin", "twin"), ("Bridge Person", "bridge"), ("Gardener", "garden")):
        runner.invoke(app, ["people", "add", name, "--substack", f"https://{slug}.substack.com"])
        runner.invoke(app, ["people", "fetch", name])
    return data_dir


def test_embed_then_similar(workspace: Path, tmp_path: Path) -> None:
    essay = tmp_path / "essay.md"
    essay.write_text("Kafka streaming billing pipelines", encoding="utf-8")
    assert runner.invoke(app, ["corpus", "add", str(essay)]).exit_code == 0

    embedded = runner.invoke(app, ["embed"])
    assert embedded.exit_code == 0
    assert "Provider: hashed/hashed-256" in embedded.stdout
    assert "Embedded: 1 corpus + 3 external" in embedded.stdout

    similar = runner.invoke(app, ["people", "similar"])
    assert similar.exit_code == 0
    assert "Closest to your corpus:" in similar.stdout
    assert similar.stdout.index("Kafka Twin") < similar.stdout.index("Gardener")

    named = runner.invoke(app, ["people", "similar", "Kafka Twin"])
    assert named.exit_code == 0
    assert "Closest to Kafka Twin:" in named.stdout

    like = runner.invoke(app, ["people", "like", "Kafka Twin", "Gardener"])
    assert like.exit_code == 0
    assert "Closest to Kafka Twin + Gardener:" in like.stdout
    # the named people are excluded; the bridge between their topics tops the list
    assert "Bridge Person" in like.stdout
    assert "Kafka Twin  score" not in like.stdout


def test_similar_before_embed_fails_with_guidance(workspace: Path) -> None:
    result = runner.invoke(app, ["people", "similar", "Kafka Twin"])
    assert result.exit_code == 1
    assert "wingman embed" in result.output


def test_doctor_reports_embedding_config(workspace: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "[ok] embeddings: hashed/hashed-256" in result.stdout
