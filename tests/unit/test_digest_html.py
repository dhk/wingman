"""The digest's styled HTML twin: same run data, design-system rendering."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

import wingman.application.focus as focus_module
from wingman.application.focus import ActionItem, OvernightTarget, overnight_run
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.digest_html import render_digest_html


def test_render_escapes_and_structures() -> None:
    now = datetime(2026, 7, 20, 5, 30, tzinfo=UTC)
    targets = [
        OvernightTarget(
            name="Acme <&> Co",
            kind="company",
            status="ok",
            lines=["research https://acme.example.com/careers: 1 new link"],
        ),
        OvernightTarget(name="Broken Corp", kind="company", status="failed", lines=["boom"]),
    ]
    actions = [
        ActionItem(
            what="Assess the new opening at Acme <&> Co",
            why="1 new link",
            who="Acme <&> Co",
            evidence=[
                "[link](https://acme.example.com/jobs/x?tracking=very-long-blob)",
                "[Supersimple raises $2.2M <PR>](https://news.google.com/rss/articles/CBMi)",
                'run: wingman assess --url "https://acme.example.com/jobs/x"',
            ],
            key="research:acme & co:https://acme.example.com/careers",
        )
    ]
    page = render_digest_html(now, targets, actions, 2, ["https://blog.example.com (2 rec)"])
    assert page.startswith("<!doctype html>")
    assert "Overnight digest — 2026-07-20" in page
    assert "Acme &lt;&amp;&gt; Co" in page and "<&>" not in page  # escaped everywhere
    assert "✓" in page and "✗" in page
    assert "2 targets" in page and "1 failures" in page and "1 actions" in page
    assert "Action list" in page and page.index("Action list") < page.index("Targets")
    assert "<code>research:acme &amp; co:https://acme.example.com/careers</code>" in page
    # markdown links render as anchors, escaped titles intact, raw commands untouched
    assert '<a href="https://acme.example.com/jobs/x?tracking=very-long-blob">link</a>' in page
    assert (
        '<a href="https://news.google.com/rss/articles/CBMi">'
        "Supersimple raises $2.2M &lt;PR&gt;</a>" in page
    )
    assert "run: wingman assess --url &quot;https://acme.example.com/jobs/x&quot;" in page
    assert "2 action(s) suppressed" in page
    assert "Consider following" in page and "blog.example.com" in page
    assert "<link" not in page and 'stylesheet: "' not in page  # fully self-contained


def test_render_empty_action_list() -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    page = render_digest_html(now, [], [], 0, [])
    assert "Nothing changed enough to act on" in page
    assert "action(s) suppressed" not in page


def test_overnight_writes_html_twin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config: Config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)

    def fake_deep(
        name: str,
        config: Config,
        storage: Storage,
        actions: list[ActionItem],
        **_kwargs: object,
    ) -> OvernightTarget:
        actions.append(ActionItem(what=f"Read {name}", why="w", who=name, key="company-posts:acme"))
        return OvernightTarget(name=name, kind="company", status="ok", lines=["fetched"])

    monkeypatch.setattr(focus_module, "_company_deep", fake_deep)
    with Storage(config.db_path) as storage:
        storage.watchlist_add("overnight", "company", "Acme")
        report = overnight_run(config, storage)
    markdown = Path(report.digest_path)
    pretty = markdown.with_suffix(".html")
    assert pretty.exists()
    page = pretty.read_text(encoding="utf-8")
    assert "Read Acme" in page and "company-posts:acme" in page
    latest = markdown.parent / "latest.html"
    assert latest.read_text(encoding="utf-8") == page
