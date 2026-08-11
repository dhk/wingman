"""The read surface (RFC-033): token gate, file confinement, uploads."""

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import wingman.webui as webui_module
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.mcp_server import _http_token, server
from wingman.providers.recorded import RecordedProvider
from wingman.webui import register_ui

RESUME = "# Jo\n\n- Shipped the search rewrite.\n\nSkills: Python.\n"
RESPONSE = json.dumps(
    {
        "items": [
            {
                "kind": "achievement",
                "name": "Search rewrite",
                "detail": "Shipped the search rewrite.",
                "classification": "fact",
                "confidence": 0.9,
                "quotes": ["Shipped the search rewrite."],
            }
        ]
    }
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    token = _http_token(config)
    register_ui(server)
    test_client = TestClient(server.streamable_http_app())
    return test_client, token


def test_wrong_or_missing_token_is_a_plain_404(client: tuple[TestClient, str]) -> None:
    http, token = client
    assert http.get("/ui/wrong-token").status_code == 404
    assert http.get(f"/ui/{token}x/file/x.html").status_code == 404
    ok = http.get(f"/ui/{token}/")
    assert ok.status_code == 200 and "Wingman" in ok.text


def test_home_lists_digest_and_reports(client: tuple[TestClient, str]) -> None:
    http, token = client
    config = load_config()
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True)
    (digests / "latest.html").write_text("<h1>MARKER-DIGEST</h1>", encoding="utf-8")
    (config.reports_dir / "packs").mkdir()
    (config.reports_dir / "packs" / "pack-staff-mle.md").write_text("pack!", encoding="utf-8")
    page = http.get(f"/ui/{token}/").text
    assert "Today" in page and "file/digests/latest.html" in page
    assert 'href="file/packs/pack-staff-mle.md"' in page  # real path in href only
    assert ">Staff Mle<" in page  # humanized link text (issue #95)
    assert "Application packs" in page  # humanized group, never the raw dir name
    assert "Upload" in page  # the form is on the page
    served = http.get(f"/ui/{token}/file/digests/latest.html")
    assert served.status_code == 200 and "MARKER-DIGEST" in served.text
    assert served.headers["content-type"].startswith("text/html")


def test_report_pages_get_a_live_nav_back_to_the_tabbed_home(
    client: tuple[TestClient, str],
) -> None:
    """A generated .html report served through /file/ gets a nav bar injected
    at serve time so there's a way back to the other tabs without hitting
    the browser's back button — but the bar is relative, not baked into the
    file on disk (the artifact stays portable if downloaded or opened raw)."""
    http, token = client
    config = load_config()
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True)
    (digests / "latest.html").write_text(
        "<!doctype html>\n<style>body{}</style>\n<div>MARKER</div>\n", encoding="utf-8"
    )
    served = http.get(f"/ui/{token}/file/digests/latest.html")
    assert served.status_code == 200
    assert 'class="wg-nav"' in served.text
    assert '<a href="../../?tab=digest">Digest</a>' in served.text
    assert '<a href="../../?tab=manage">Manage</a>' in served.text
    assert "MARKER" in served.text  # original content is untouched, just prefixed
    # the file on disk was never modified
    assert "wg-nav" not in (digests / "latest.html").read_text(encoding="utf-8")


def test_report_nav_skipped_for_pages_without_the_expected_style_shell(
    client: tuple[TestClient, str],
) -> None:
    """A .html file with no '</style>' marker (e.g. a bare fixture) is served
    as-is rather than guessing where injection should go."""
    http, token = client
    config = load_config()
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True)
    (digests / "latest.html").write_text("<h1>MARKER-DIGEST</h1>", encoding="utf-8")
    served = http.get(f"/ui/{token}/file/digests/latest.html")
    assert served.status_code == 200
    assert served.text == "<h1>MARKER-DIGEST</h1>"
    assert "wg-nav" not in served.text


def test_report_nav_is_not_added_to_non_html_files(client: tuple[TestClient, str]) -> None:
    http, token = client
    config = load_config()
    (config.reports_dir / "career.md").write_text("# Career\n<style>x</style>\n", encoding="utf-8")
    served = http.get(f"/ui/{token}/file/career.md")
    assert served.status_code == 200
    assert "wg-nav" not in served.text


def test_stylesheet_is_not_listed_as_a_report(client: tuple[TestClient, str]) -> None:
    """Issue #141: wingman-pdf.css rides along every export dir but isn't a report."""
    from wingman.reporting.export import STYLESHEET_NAME

    http, token = client
    config = load_config()
    pdf_dir = config.reports_dir / "pdf"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / STYLESHEET_NAME).write_text("body{}", encoding="utf-8")
    (pdf_dir / "career.md").write_text("# Career Profile\n", encoding="utf-8")
    page = http.get(f"/ui/{token}/").text
    assert "Wingman Pdf" not in page  # the mis-titled row is gone
    assert STYLESHEET_NAME not in page  # not linked as a row at all
    assert ">Career<" in page  # the real report still lists
    # still servable if directly linked (e.g. from an export's own frontmatter)
    served = http.get(f"/ui/{token}/file/pdf/{STYLESHEET_NAME}")
    assert served.status_code == 200


def test_value_radar_svg_is_listed_and_served(client: tuple[TestClient, str]) -> None:
    """v3 of issue #240: a chart written under reports/charts/ by
    `reporting.radar.export_value_radar` (CLI `values-chart` / MCP
    `values_chart`) surfaces through the same generic reports_dir listing
    every other export uses — no bespoke webui route needed."""
    http, token = client
    config = load_config()
    charts = config.reports_dir / "charts"
    charts.mkdir(parents=True)
    (charts / "your-corpus-values-radar.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><title>MARKER-CHART</title></svg>',
        encoding="utf-8",
    )
    page = http.get(f"/ui/{token}/").text
    assert "file/charts/your-corpus-values-radar.svg" in page
    assert "Value charts" in page  # humanized group name, never the raw dir
    served = http.get(f"/ui/{token}/file/charts/your-corpus-values-radar.svg")
    assert served.status_code == 200
    assert "MARKER-CHART" in served.text
    assert served.headers["content-type"].startswith("image/svg+xml")


def test_json_sidecar_is_deduped_against_its_md_twin(client: tuple[TestClient, str]) -> None:
    """Issue #141: career.md + career.json (and fit-brief-*.md/.json) list once."""
    http, token = client
    config = load_config()
    (config.reports_dir / "career.md").write_text("# Career Profile\n", encoding="utf-8")
    (config.reports_dir / "career.json").write_text("{}", encoding="utf-8")
    page = http.get(f"/ui/{token}/").text
    assert page.count(">Career<") == 1
    assert 'href="file/career.md"' in page
    assert 'href="file/career.json"' not in page
    # still directly servable
    served = http.get(f"/ui/{token}/file/career.json")
    assert served.status_code == 200


def test_report_listing_prefers_frontmatter_title(client: tuple[TestClient, str]) -> None:
    """Issue #141: a `title:` field in frontmatter wins over the filename guess.

    This is the same mechanism that already fixes pack.py's titles, which
    were being overridden by the generic filename-derived heuristic even
    though a real custom title sits right there in frontmatter.
    """
    http, token = client
    config = load_config()
    packs = config.reports_dir / "packs"
    packs.mkdir(parents=True)
    (packs / "pack-staff-mle.md").write_text(
        "---\ntitle: Application pack — Staff MLE @ Acme\n---\n\nbody\n", encoding="utf-8"
    )
    page = http.get(f"/ui/{token}/").text
    assert ">Application pack — Staff MLE @ Acme<" in page
    assert ">Staff Mle<" not in page  # filename heuristic no longer wins


def test_file_serving_is_confined_to_reports(client: tuple[TestClient, str]) -> None:
    http, token = client
    config = load_config()
    # a secret OUTSIDE reports/ must be unreachable, traversal or not
    (config.data_dir / "mcp-http-token").exists()  # the crown jewel next door
    assert http.get(f"/ui/{token}/file/../mcp-http-token").status_code == 404
    assert http.get(f"/ui/{token}/file/%2e%2e/mcp-http-token").status_code == 404
    assert http.get(f"/ui/{token}/file/nope.html").status_code == 404
    # unsupported suffixes are refused even inside reports/
    (config.reports_dir / "x.sqlite").write_text("db", encoding="utf-8")
    assert http.get(f"/ui/{token}/file/x.sqlite").status_code == 404


def test_upload_linkedin_zip_imports(client: tuple[TestClient, str]) -> None:
    http, token = client
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Positions.csv",
            "Company Name,Title,Description,Location,Started On,Finished On\nAcme,Head of Data,Built it.,SF,Jan 2020,\n",
        )
        archive.writestr("Skills.csv", "Name\nPython\n")
    response = http.post(
        f"/ui/{token}/upload",
        files={"file": ("linkedin-export.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200
    assert "1 positions" in response.text and "1 skills" in response.text
    config = load_config()
    with Storage(config.db_path) as storage:
        assert storage.count_profile_items() == 2


def test_upload_resume_ingests(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    import wingman.providers.router as router_module

    monkeypatch.setattr(
        router_module, "get_provider", lambda capability, config: RecordedProvider(RESPONSE)
    )
    response = http.post(
        f"/ui/{token}/upload",
        files={"file": ("resume.md", RESUME.encode(), "text/markdown")},
    )
    assert response.status_code == 200 and "Accepted: 1" in response.text


def test_upload_rejects_junk(client: tuple[TestClient, str]) -> None:
    http, token = client
    response = http.post(
        f"/ui/{token}/upload", files={"file": ("malware.exe", b"MZ", "application/x-thing")}
    )
    assert response.status_code == 200 and "Unsupported type" in response.text
    big = b"x" * (webui_module.MAX_UPLOAD_BYTES + 1)
    response = http.post(f"/ui/{token}/upload", files={"file": ("resume.md", big, "text/plain")})
    assert "over the 20 MB limit" in response.text
    assert (
        http.post("/ui/bad/upload", files={"file": ("a.md", b"x", "text/plain")}).status_code == 404
    )


def test_key_form_validates_before_storing(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    config = load_config()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    calls: list[tuple[str, str]] = []

    def good(value: str) -> None:
        calls.append(("ok", value))

    def bad(value: str) -> str:
        calls.append(("bad", value))
        return "rejected the key (authentication failed)."

    monkeypatch.setitem(webui_module.VALIDATORS, "anthropic", good)
    monkeypatch.setitem(webui_module.VALIDATORS, "voyage", bad)

    page = http.get(f"/ui/{token}/").text
    assert "API keys" in page and "not set" in page

    response = http.post(f"/ui/{token}/keys", data={"anthropic": "sk-ant-good", "voyage": "pa-bad"})
    assert response.status_code == 200
    assert "anthropic: verified and live now." in response.text
    assert "voyage: rejected the key" in response.text and "Nothing was stored" in response.text

    from wingman.infrastructure.keys import read_workspace_keys, workspace_keys_path

    stored = read_workspace_keys(config.data_dir)
    assert stored == {"ANTHROPIC_API_KEY": "sk-ant-good"}  # only the verified key
    assert (workspace_keys_path(config.data_dir).stat().st_mode & 0o777) == 0o600
    import os

    from wingman.infrastructure.keys import resolve_provider_key

    # RFC-048: storing a key must never mutate process-wide os.environ (a
    # shared multi-tenant process would leak one tenant's key to every
    # other tenant) — "live immediately" instead means the next resolution
    # reads the workspace file fresh, with no process-wide side effect.
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert resolve_provider_key("ANTHROPIC_API_KEY", config.data_dir) == "sk-ant-good"

    empty = http.post(f"/ui/{token}/keys", data={})
    assert "No key was entered" in empty.text
    assert http.post("/ui/nope/keys", data={"anthropic": "x"}).status_code == 404


def test_a_submitted_key_takes_precedence_over_the_environment(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BYOK. The form previously stored the key and told the user the
    service environment would win anyway — a form that stores a key it
    will not use is a trap."""
    from wingman.infrastructure.keys import resolve_provider_key

    http, token = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    monkeypatch.setitem(webui_module.VALIDATORS, "anthropic", lambda value: None)

    response = http.post(f"/ui/{token}/keys", data={"anthropic": "sk-ant-newer"})

    assert "environment variable wins" not in response.text
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-env"  # env untouched
    assert resolve_provider_key("ANTHROPIC_API_KEY", load_config().data_dir) == "sk-ant-newer"


def test_restart_route_rejects_a_wrong_token(client: tuple[TestClient, str]) -> None:
    http, _token = client
    assert http.post("/ui/nope/restart").status_code == 404


def test_restart_route_reports_unavailable_when_not_systemd_managed(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    monkeypatch.setattr(
        "wingman.infrastructure.self_restart.systemd_manages_this_instance", lambda: False
    )
    calls: list[object] = []
    monkeypatch.setattr(
        "wingman.infrastructure.self_restart.trigger_restart", lambda: calls.append(1)
    )
    response = http.post(f"/ui/{token}/restart")
    assert response.status_code == 200
    assert "Not systemd-managed" in response.text
    assert calls == []  # never fired when there's no supervisor to bring it back


def test_restart_route_triggers_systemctl_when_systemd_managed(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    monkeypatch.setattr(
        "wingman.infrastructure.self_restart.systemd_manages_this_instance", lambda: True
    )
    calls: list[object] = []
    monkeypatch.setattr(
        "wingman.infrastructure.self_restart.trigger_restart", lambda: calls.append(1)
    )
    response = http.post(f"/ui/{token}/restart")
    assert response.status_code == 200
    assert "Restarting now" in response.text
    assert calls == [1]


def test_manage_panel_shows_restart_button_when_systemd_managed(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    monkeypatch.setattr(
        "wingman.infrastructure.self_restart.systemd_manages_this_instance", lambda: True
    )
    page = http.get(f"/ui/{token}/").text
    assert 'action="restart"' in page
    assert "Restart server" in page


def test_ui_is_path_mount_agnostic(client: tuple[TestClient, str]) -> None:
    """Behind 'tailscale serve --set-path /trent' the prefix is stripped before
    the backend: everything must work with zero prefix knowledge — a RELATIVE
    redirect to the slash landing, and only relative URLs in the page."""
    http, token = client
    bare = http.get(f"/ui/{token}", follow_redirects=False)
    assert bare.status_code == 307
    assert bare.headers["location"] == f"{token}/"  # relative: the browser keeps the prefix
    followed = http.get(f"/ui/{token}", follow_redirects=True)
    assert followed.status_code == 200 and "Wingman" in followed.text
    page = http.get(f"/ui/{token}/").text
    assert 'href="/' not in page and 'action="/' not in page  # no absolute self-URLs
    assert 'action="upload"' in page and 'action="keys"' in page
    # wrong token gets no redirect breadcrumb either
    assert http.get(f"/ui/{token}x", follow_redirects=False).status_code == 404


def test_native_prefix_serves_and_root_unaffected(client: tuple[TestClient, str]) -> None:
    """--prefix /trent: the server itself listens on the folder (RFC-033 addendum)."""
    _http, token = client
    register_ui(server, prefix="/trent")  # coexists with the root registration
    http2 = TestClient(server.streamable_http_app())
    page = http2.get(f"/trent/ui/{token}/")
    assert page.status_code == 200 and "Wingman" in page.text
    assert 'href="/' not in page.text  # still only relative URLs
    bare = http2.get(f"/trent/ui/{token}", follow_redirects=False)
    assert bare.status_code == 307 and bare.headers["location"] == f"{token}/"
    assert http2.get("/trent/ui/wrong-token/").status_code == 404
    assert http2.get(f"/ui/{token}/").status_code == 200  # root registration intact

    from wingman.webui import normalize_prefix

    assert normalize_prefix("trent") == "/trent"
    assert normalize_prefix("/trent/") == "/trent"
    assert normalize_prefix("") == "" and normalize_prefix("/") == ""


def test_connect_tab_css_actually_reveals_its_panel(client: tuple[TestClient, str]) -> None:
    """Regression: adding a tab to the Python tuple list isn't enough — the
    hand-written :checked CSS selectors must name it too, or the panel stays
    display:none forever (issue: Connect tab rendered but showed nothing)."""
    http, token = client
    page = http.get(f"/ui/{token}/").text
    assert "#tab-connect:checked ~ .tabpanel-connect" in page
    assert '#tab-connect:checked ~ .tabbar label[for="tab-connect"]' in page


def test_changelog_tab_css_actually_reveals_its_panel(client: tuple[TestClient, str]) -> None:
    """Same regression class as the Connect tab: the CSS selectors must name
    'changelog' too, or the panel stays display:none forever."""
    http, token = client
    page = http.get(f"/ui/{token}/").text
    assert "#tab-changelog:checked ~ .tabpanel-changelog" in page
    assert '#tab-changelog:checked ~ .tabbar label[for="tab-changelog"]' in page


def test_changelog_tab_shows_counts_and_curated_titles(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #145: the tab label carries today/7-day counts (user-facing
    entries only), and every entry's title renders verbatim with its PR
    number — including internal-only ones, tagged for the client-side
    filter rather than excluded server-side (see the next test)."""
    from datetime import UTC, datetime, timedelta

    import wingman.changelog_data as data_module

    today = datetime.now(UTC).date()
    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (
            (today.isoformat(), 200, "Add a brand new feature"),
            ((today - timedelta(days=3)).isoformat(), 199, "Fix a real bug"),
            ((today - timedelta(days=3)).isoformat(), 198, "docs: session snapshot for resume"),
            ((today - timedelta(days=30)).isoformat(), 100, "Old feature, outside the week"),
        ),
    )
    http, token = client
    page = http.get(f"/ui/{token}/").text
    assert "Changelog (1 new today / 2 last 7 days)" in page
    assert "Add a brand new feature" in page and "#200" in page
    assert "Fix a real bug" in page and "#199" in page
    assert "session snapshot" in page  # present in the DOM, filtered client-side (below)
    assert "Old feature, outside the week" in page  # listed, just outside the counted window


def test_changelog_filter_tags_rows_by_user_facing_and_renders_controls(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """User Facing / System Features / All: each row is tagged with which
    the CSS filter hides under which selection, and all three controls are
    present — a client-side filter with no controls, or one that doesn't
    actually distinguish rows, is silently broken."""
    from datetime import UTC, datetime, timedelta

    import wingman.changelog_data as data_module

    today = datetime.now(UTC).date()
    monkeypatch.setattr(
        data_module,
        "CHANGELOG_DATA",
        (
            (today.isoformat(), 200, "Add a brand new feature"),
            ((today - timedelta(days=1)).isoformat(), 199, "chore: bump lockfile"),
        ),
    )
    http, token = client
    page = http.get(f"/ui/{token}/").text
    # controls: three radios, defaulting to User Facing, plus their labels
    assert '<input type="radio" name="cl-filter" id="cl-uf" checked>' in page
    assert '<input type="radio" name="cl-filter" id="cl-sys">' in page
    assert '<input type="radio" name="cl-filter" id="cl-all">' in page
    assert '<label for="cl-uf">User Facing</label>' in page
    assert '<label for="cl-sys">System Features</label>' in page
    assert '<label for="cl-all">All</label>' in page
    # the CSS rules that actually do the hiding under each selection
    assert "#cl-uf:checked ~ .changelog-rows .cl-sys" in page
    assert "#cl-sys:checked ~ .changelog-rows .cl-uf" in page
    # rows tagged by category — user-facing gets cl-uf, internal gets cl-sys
    assert '<div class="changelog-row cl-uf">' in page  # "Add a brand new feature"
    assert '<div class="changelog-row cl-sys">' in page  # "chore: bump lockfile"


def test_connect_tab_shows_loopback_urls_with_no_tunnel_hint(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wingman.mcp_server._extra_allowed_hosts", lambda cli_hosts: [])
    http, token = client
    host, port = server.settings.host, server.settings.port
    page = http.get(f"/ui/{token}/").text
    assert '<label for="tab-connect">Connect</label>' in page
    assert f'value="http://{host}:{port}/mcp/{token}"' in page
    assert f'value="http://{host}:{port}/ui/{token}"' in page
    assert "No tunnel hostname detected" in page
    assert "Settings → Connectors → Add custom connector" in page


def test_connect_tab_shows_tunnel_urls_when_a_hostname_is_detected(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "wingman.mcp_server._extra_allowed_hosts",
        lambda cli_hosts: ["lobster.tail08dfce.ts.net"],
    )
    http, token = client
    page = http.get(f"/ui/{token}/").text
    assert f"https://lobster.tail08dfce.ts.net/mcp/{token}" in page
    assert f"https://lobster.tail08dfce.ts.net/ui/{token}/" in page
    assert "No tunnel hostname detected" not in page


def test_connect_tab_honors_wingman_tunnel_port_env(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two instances sharing one Tailscale hostname on distinct funnel ports
    (issue: dhk's and Trent's funnel commands collided on the default 443
    root) — WINGMAN_TUNNEL_PORT lets each instance's Connect tab print its
    own tunnel's actual external port."""
    monkeypatch.setattr(
        "wingman.mcp_server._extra_allowed_hosts",
        lambda cli_hosts: ["lobster.tail08dfce.ts.net"],
    )
    monkeypatch.setenv("WINGMAN_TUNNEL_PORT", "8443")
    http, token = client
    page = http.get(f"/ui/{token}/").text
    assert f"https://lobster.tail08dfce.ts.net:8443/mcp/{token}" in page
    assert f"https://lobster.tail08dfce.ts.net:8443/ui/{token}/" in page


def test_connect_tab_respects_native_prefix(client: tuple[TestClient, str]) -> None:
    _http, token = client
    host, port = server.settings.host, server.settings.port
    register_ui(server, prefix="/trent")
    http2 = TestClient(server.streamable_http_app())
    page = http2.get(f"/trent/ui/{token}/").text
    assert f'value="http://{host}:{port}/trent/mcp/{token}"' in page
    assert f'value="http://{host}:{port}/trent/ui/{token}"' in page


def test_connect_panel_appears_in_fresh_setup_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "fresh-ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    token = _http_token(config)
    register_ui(server)
    http = TestClient(server.streamable_http_app())
    page = http.get(f"/ui/{token}/").text
    assert "03 — Connect a Claude client" in page
    assert f'value="http://{server.settings.host}:{server.settings.port}/mcp/{token}"' in page


def test_spec_three_states_and_dark_tokens(client: tuple[TestClient, str]) -> None:
    """Issue #95 acceptance: state machine, humanization, group labels, dark mode."""
    http, token = client
    config = load_config()
    # fixture made the db but no digest -> State 3: degraded, Manage visible
    page = http.get(f"/ui/{token}/").text
    assert "No digest yet" in page and "wingman overnight" in page
    assert (
        '<div class="manage"><div class="manage-hd"><span class="manage-title">Manage</span>'
        in page
    )
    assert "prefers-color-scheme: dark" in page  # dark tokens ride every page
    # a digest arrives -> State 1: hero first, Manage still plain (no caret)
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True, exist_ok=True)
    (digests / "latest.html").write_text("<h1>d</h1>", encoding="utf-8")
    (digests / "overnight-20260101T051500Z.html").write_text("<h1>old</h1>", encoding="utf-8")
    page = http.get(f"/ui/{token}/").text
    assert "Overnight digest" in page and 'class="hero"' in page
    assert (
        '<div class="manage"><div class="manage-hd"><span class="manage-title">Manage</span>'
        in page
    )
    assert "<details" not in page  # no collapse/caret for Manage
    assert "Overnight digests" in page  # group label
    assert "01 Jan" in page  # humanized date from the stamp
    assert ">overnight-20260101T051500Z<" not in page  # raw filename never link text
    assert "latest.html" in page and page.count("file/digests/latest.html") == 1  # hero only


def test_spec_fresh_state_guides_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No database at all -> State 2: guided setup, no dead sections."""
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "fresh-ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    token = _http_token(config)
    register_ui(server)
    http = TestClient(server.streamable_http_app())
    page = http.get(f"/ui/{token}/").text
    assert "Set up your workspace" in page
    assert "01" in page and "02" in page  # the two steps
    assert "<details" not in page  # nothing collapsed away in setup
    assert 'action="keys"' in page and 'action="upload"' in page
    assert 'href="/' not in page  # still relative everywhere
    # State 2 gets no tab chrome — desktop centers the setup column instead
    assert 'type="radio"' not in page and 'class="tabbar"' not in page
    assert '<div class="ui ui-setup">' in page
    assert "<script" not in page  # the page carries zero JavaScript


def test_desktop_tabs_are_css_only_and_single_sourced(client: tuple[TestClient, str]) -> None:
    """Spec section 6 / acceptance 9: Digest · Files · Manage tabs from hidden
    radios, hero pinned above the bar, panels present exactly once (narrow
    viewports stack the same content — nothing is duplicated per tab)."""
    http, token = client
    config = load_config()
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True)
    (digests / "latest.html").write_text("<h1>d</h1>", encoding="utf-8")
    (digests / "overnight-20260101T051500Z.html").write_text("<h1>old</h1>", encoding="utf-8")
    (config.reports_dir / "packs").mkdir()
    (config.reports_dir / "packs" / "pack-staff-mle.md").write_text("pack!", encoding="utf-8")
    page = http.get(f"/ui/{token}/").text
    assert "<script" not in page  # CSS-only: no JS tab controller, no JS at all
    assert '<input type="radio" name="view" id="tab-digest" checked>' in page
    assert 'id="tab-files"' in page and 'id="tab-manage"' in page
    assert '<label for="tab-digest">Digest</label>' in page
    assert '<label for="tab-files">Files</label>' in page
    assert '<label for="tab-manage">Manage</label>' in page
    # header and hero stay above the tab bar (the glance is never behind a tab)
    assert page.index('class="hdr"') < page.index('class="hero"') < page.index('class="tabbar"')
    # each panel exists exactly once — CSS shows/hides, content is never cloned
    assert page.count('class="tabpanel tabpanel-digest"') == 1
    assert page.count('class="tabpanel tabpanel-files"') == 1
    assert page.count('class="tabpanel tabpanel-manage"') == 1
    assert page.count("Application packs") == 1
    assert page.count("Overnight digests") == 1
    assert page.count('action="upload"') == 1  # one Manage, not one per layout
    # Manage renders plain (no caret/disclosure) inside the tab panel
    assert (
        '<div class="manage"><div class="manage-hd"><span class="manage-title">Manage</span>'
        in page
    )
    assert "<details" not in page


def test_tab_query_param_preselects_a_tab(client: tuple[TestClient, str]) -> None:
    """A report page's nav bar links to '?tab=<key>' — the home page must
    honor it, so clicking Manage from inside a digest actually lands on
    Manage instead of always resetting to Digest."""
    http, token = client
    config = load_config()
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True)
    (digests / "latest.html").write_text("<h1>d</h1>", encoding="utf-8")
    # latest.html alone doesn't populate the Digest tab's row list (only dated
    # files do — 'latest' is reached via the hero link instead).
    (digests / "overnight-20260101T051500Z.html").write_text("<h1>old</h1>", encoding="utf-8")
    page = http.get(f"/ui/{token}/?tab=manage").text
    assert '<input type="radio" name="view" id="tab-manage" checked>' in page
    assert '<input type="radio" name="view" id="tab-digest" checked>' not in page

    # an unknown tab key falls back to the first panel, same as no param at all
    fallback = http.get(f"/ui/{token}/?tab=nonsense").text
    assert '<input type="radio" name="view" id="tab-digest" checked>' in fallback


def test_degraded_state_tabs_skip_empty_digest_panel(client: tuple[TestClient, str]) -> None:
    """State 3 with only packs: no dead Digest tab; Files + Manage still tab."""
    http, token = client
    config = load_config()
    (config.reports_dir / "packs").mkdir(parents=True)
    (config.reports_dir / "packs" / "pack-staff-mle.md").write_text("pack!", encoding="utf-8")
    page = http.get(f"/ui/{token}/").text
    assert "No digest yet" in page
    assert 'id="tab-digest"' not in page  # empty panel -> no tab for it
    assert '<input type="radio" name="view" id="tab-files" checked>' in page
    assert 'id="tab-manage"' in page
    assert (
        '<div class="manage"><div class="manage-hd"><span class="manage-title">Manage</span>'
        in page
    )  # still visible in State 3


def test_group_rows_cap_at_twelve_with_older_line(client: tuple[TestClient, str]) -> None:
    """Spec section 5: 12 rows per group, newest first, then a muted 'older…'."""
    import os

    http, token = client
    config = load_config()
    packs = config.reports_dir / "packs"
    packs.mkdir(parents=True)
    base = 1_700_000_000
    for index in range(14):
        path = packs / f"pack-item-{index:02d}.md"
        path.write_text("pack!", encoding="utf-8")
        os.utime(path, (base + index, base + index))  # item-13 newest
    page = http.get(f"/ui/{token}/").text
    assert page.count('class="row"') == 12  # capped
    assert "older…" in page  # the cap is named, mutedly
    assert ">Item 13<" in page and ">Item 02<" in page  # newest 12 survive
    assert ">Item 01<" not in page and ">Item 00<" not in page  # oldest two dropped
    assert "pack-item-00" not in page and "pack-item-01" not in page  # no raw names


def test_digest_rows_cap_at_seven_not_the_generic_twelve(client: tuple[TestClient, str]) -> None:
    """#173: digests use their own cap ("last N overnight runs") — smaller
    than the generic per-section cap, since one digest file = one run."""
    import os

    http, token = client
    config = load_config()
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True)
    base = 1_700_000_000
    for index in range(9):
        path = digests / f"overnight-202607{10 + index:02d}T050000Z.md"
        path.write_text("digest!", encoding="utf-8")
        os.utime(path, (base + index, base + index))  # index 8 newest
    page = http.get(f"/ui/{token}/").text
    assert page.count('class="row"') == 7  # capped at 7, not the generic 12
    assert "older…" in page


def test_design_tokens_are_one_shared_constant(client: tuple[TestClient, str]) -> None:
    """Issue #118 section 9: the same token block feeds the UI and the exports."""
    from wingman.reporting.design_tokens import DESIGN_TOKENS_CSS
    from wingman.reporting.export import WINGMAN_PDF_CSS

    assert "--accent: #2b50e8" in DESIGN_TOKENS_CSS  # Electric Cobalt, light
    assert "prefers-color-scheme: dark" in DESIGN_TOKENS_CSS  # dark rides along
    assert DESIGN_TOKENS_CSS in WINGMAN_PDF_CSS  # exports/digest twin surface
    assert DESIGN_TOKENS_CSS in webui_module._UI_CSS  # web UI surface
    http, token = client
    page = http.get(f"/ui/{token}/").text
    assert page.count("--accent: #2b50e8") == 1  # emitted once, from one source


# --- profile page (#284) ----------------------------------------------


def _add_item(config, **kwargs) -> None:
    from wingman.domain.profile import EvidenceSpan, ProfileItem

    defaults = {
        "classification": "fact",
        "confidence": 1.0,
        "evidence": [EvidenceSpan(source_record_id="rec-12345678", quote="cutting latency")],
        "prompt_version": "v2",
        "extracted_by": "test",
    }
    with Storage(config.db_path) as storage:
        storage.add_profile_item(ProfileItem(**{**defaults, **kwargs}))


def test_profile_page_shows_each_claim_with_its_evidence(
    client: tuple[TestClient, str], tmp_path: Path
) -> None:
    """career.md is reachable here already, but flat: it cannot show what
    backs a claim without leaving the page (#284)."""
    from wingman.domain.profile import ProfileItemKind

    http, token = client
    _add_item(
        load_config(),
        kind=ProfileItemKind.ACHIEVEMENT,
        name="Shipped the search rewrite",
        detail="Cut latency 24h to 20m.",
    )

    page = http.get(f"/ui/{token}/profile").text

    assert "Shipped the search rewrite" in page
    assert "Cut latency 24h to 20m." in page
    assert "cutting latency" in page, "the evidence quote belongs on the page"
    assert "rec-1234" in page, "and the record it came from"


def test_profile_page_flags_claims_worth_a_second_look(
    client: tuple[TestClient, str],
) -> None:
    """An inference and a fact look identical in career.md."""
    from wingman.domain.profile import ProfileItemKind

    http, token = client
    _add_item(
        load_config(),
        kind=ProfileItemKind.SKILL,
        name="Kubernetes",
        classification="inference",
        confidence=0.6,
    )

    page = http.get(f"/ui/{token}/profile").text

    assert "Needs attention" in page
    assert "inference" in page
    assert "confidence 0.60" in page
    assert "one quote" in page


def test_profile_page_names_superseded_items(client: tuple[TestClient, str]) -> None:
    """An ingest reported 'Retired: 23' and the only way to learn which was
    to ask. RFC-028 keeps the lineage; nothing surfaced it."""
    from wingman.domain.profile import ItemStatus, ProfileItemKind

    http, token = client
    _add_item(
        load_config(),
        kind=ProfileItemKind.SKILL,
        name="An older claim",
        status=ItemStatus.SUPERSEDED,
    )

    page = http.get(f"/ui/{token}/profile").text

    assert "Superseded" in page
    assert "An older claim" in page


def test_profile_page_puts_both_sides_of_a_conflict_together(
    client: tuple[TestClient, str],
) -> None:
    """Resolving a conflict means comparing two claims; career.md lists one
    of them under its kind and the other in a footnote-shaped block."""
    from wingman.domain.profile import ItemStatus, ProfileItemKind

    config = load_config()
    _add_item(config, kind=ProfileItemKind.SKILL, name="BigQuery", detail="the incumbent")
    with Storage(config.db_path) as storage:
        incumbent = storage.list_profile_items()[0]
    _add_item(
        config,
        kind=ProfileItemKind.SKILL,
        name="BigQuery",
        detail="the challenger",
        status=ItemStatus.CONFLICT,
        conflicts_with=incumbent.item_id,
    )

    http, token = client
    page = http.get(f"/ui/{token}/profile").text

    assert "conflicts with" in page
    assert "the incumbent" in page and "the challenger" in page


def test_profile_page_needs_the_capability_path(client: tuple[TestClient, str]) -> None:
    http, _token = client
    assert http.get("/ui/not-the-token/profile").status_code == 404


def test_profile_page_says_what_is_missing_and_what_it_costs(
    client: tuple[TestClient, str],
) -> None:
    """'Roles: 0' is a number. The reason to care is that nothing then
    shows tenure or seniority — which is what blocked two role framings on
    a real profile for a day."""
    from wingman.domain.profile import ProfileItemKind

    http, token = client
    _add_item(load_config(), kind=ProfileItemKind.SKILL, name="Python")

    page = http.get(f"/ui/{token}/profile").text

    assert "No roles" in page and "tenure" in page
    assert "No testimonials" in page
    assert "No job criteria" in page and "unscored" in page
    assert "1 skill" in page and "1 skills" not in page


def test_completeness_band_goes_quiet_once_a_section_is_filled(
    client: tuple[TestClient, str],
) -> None:
    """A checklist that keeps nagging about solved problems stops being read."""
    from wingman.application.job_scoring import criteria_path
    from wingman.domain.profile import ProfileItemKind

    config = load_config()
    _add_item(config, kind=ProfileItemKind.ROLE, name="Head of Data, Acme")
    criteria_path(config).write_text("# criteria\n\nRemote only.\n", encoding="utf-8")

    http, token = client
    page = http.get(f"/ui/{token}/profile").text

    assert "No roles" not in page
    assert "1 role" in page and "1 roles" not in page
    assert "Job criteria set" in page
    assert "unscored" not in page


def test_render_profile_html_is_a_standalone_document(
    client: tuple[TestClient, str],
) -> None:
    """The export path for a locally-running client: Claude runs on the
    user's machine and talks to Wingman remotely, so the session holds both
    halves — this returns the markup, the local side saves it."""
    from wingman.domain.profile import ProfileItemKind
    from wingman.webui import render_profile_html

    _add_item(load_config(), kind=ProfileItemKind.ACHIEVEMENT, name="Shipped the rewrite")

    html = render_profile_html(load_config())

    assert html.lstrip().startswith("<!doctype html>"), "must open in a browser on its own"
    assert "<style" in html, "self-contained: no stylesheet to fetch from the server"
    assert "Shipped the rewrite" in html
    assert "cutting latency" in html, "evidence travels with the claim"


def test_the_exported_page_carries_no_capability_token(
    client: tuple[TestClient, str],
) -> None:
    """This file gets saved to a laptop and sometimes forwarded. The
    token that reaches the page must not be inside it."""
    from wingman.webui import render_profile_html

    _http, token = client
    assert token not in render_profile_html(load_config())


def test_interview_answers_are_not_counted_as_thin_evidence(
    client: tuple[TestClient, str],
) -> None:
    """Interview nominations carry exactly one evidence span by
    construction and nothing ever adds a second. Counting them meant
    'N claims rest on a single quote' could never go green for anyone who
    used the interview — the nagging-forever failure the band exists to
    avoid — and named items that appear in none of the page's sections."""
    from wingman.domain.profile import ProfileItemKind

    config = load_config()
    _add_item(config, kind=ProfileItemKind.ROLE, name="Head of Data, Acme")
    _add_item(config, kind=ProfileItemKind.ACHIEVEMENT, name="Shipped it")
    _add_item(config, kind=ProfileItemKind.SKILL, name="Python")
    _add_item(config, kind=ProfileItemKind.TESTIMONIAL, name="She said so")
    for n in range(5):
        _add_item(config, kind=ProfileItemKind.INTERVIEW, name=f"nomination {n}")

    http, token = client
    page = http.get(f"/ui/{token}/profile").text

    # Four reported claims each rest on one quote; the five interview items
    # must not be added to that number.
    assert "4 claims resting on a single quote" in page
    assert "9 claims" not in page


def test_every_empty_section_names_its_consequence(client: tuple[TestClient, str]) -> None:
    """The band's own docstring says a bare count is useless. Two rows
    printed '0 achievements' and '0 skills' while the rest explained
    themselves."""
    http, token = client
    page = http.get(f"/ui/{token}/profile").text

    assert "No achievements — nothing here says what you actually did" in page
    assert "No skills — nothing to match against" in page
    assert "0 achievements" not in page and "0 skills" not in page


def test_the_export_does_not_disclose_the_host_workspace_path(
    client: tuple[TestClient, str],
) -> None:
    """The served page shows the workspace path in its header; the export
    is a file that gets saved to a laptop and forwarded, and the path
    names the host account."""
    from wingman.webui import render_profile_html

    config = load_config()
    http, token = client

    served = http.get(f"/ui/{token}/profile").text
    exported = render_profile_html(config)

    assert str(config.data_dir) in served, "the served page keeps it — it never leaves the host"
    assert str(config.data_dir) not in exported


def test_the_band_and_the_completeness_tool_cannot_disagree(
    client: tuple[TestClient, str],
) -> None:
    """One measurement, one answer. The band used to walk the items itself,
    which meant two implementations of 'how many roles' that could drift
    apart silently — the page saying one thing and `completeness` another."""
    from wingman.application.completeness import compute_completeness
    from wingman.domain.profile import ProfileItemKind
    from wingman.infrastructure.storage import Storage

    config = load_config()
    for n in range(3):
        _add_item(config, kind=ProfileItemKind.ROLE, name=f"Role {n}")
    _add_item(config, kind=ProfileItemKind.SKILL, name="Python")

    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)

    http, token = client
    page = http.get(f"/ui/{token}/profile").text

    assert f"{report.career.roles} roles" in page
    assert report.career.roles == 3
    assert "1 skill" in page and report.career.skills == 1


def test_the_home_page_links_to_the_profile_page(client: tuple[TestClient, str]) -> None:
    """The profile page shipped as a route nothing linked to, so the only way
    to reach it was to know the URL and type it. Spotted on a real tenant's
    home page, which offered Digest / Files / Changelog / Connect / Manage and
    no way at all to see the profile."""
    http, token = client

    page = http.get(f"/ui/{token}/").text

    assert 'href="profile"' in page
    assert "Profile" in page


def test_the_profile_link_survives_a_mount_prefix(client: tuple[TestClient, str]) -> None:
    """It is relative for the same reason every other link here is: the tunnel
    fronting this serves it under /shared, which the process itself never sees.
    An absolute /ui/... href would 404 at the tunnel."""
    http, token = client

    page = http.get(f"/ui/{token}/").text

    assert 'href="/ui/' not in page


def test_the_profile_page_is_reachable_from_the_link_the_home_page_gives(
    client: tuple[TestClient, str],
) -> None:
    """Relative hrefs resolve against the home page's own URL, so this only
    works because home is served with its trailing slash."""
    http, token = client

    home = http.get(f"/ui/{token}/")
    assert home.status_code == 200

    followed = http.get(f"/ui/{token}/profile")
    assert followed.status_code == 200
    assert "Profile" in followed.text


def test_the_home_page_links_to_the_progress_view(client: tuple[TestClient, str]) -> None:
    """Someone working through setup wants to see how far along they are
    without knowing a tool name — the completeness report existed only as a
    tool response, and only wrote a page if you knew to pass as_html."""
    http, token = client

    page = http.get(f"/ui/{token}/").text

    assert 'href="completeness"' in page
    assert "Progress" in page


def test_the_progress_view_renders_the_completeness_report(
    client: tuple[TestClient, str],
) -> None:
    from wingman.domain.profile import ProfileItemKind

    config = load_config()
    _add_item(config, kind=ProfileItemKind.ROLE, name="Staff Analyst")

    http, token = client
    page = http.get(f"/ui/{token}/completeness")

    assert page.status_code == 200
    assert "Career Profile" in page.text
    assert "Job Criteria" in page.text
    # Interview progress, against the cap application.interview enforces —
    # the one section here with a denominator that is not invented.
    assert "Interview" in page.text
    assert "values_pro" in page.text
    assert "Applications" in page.text
    # Nothing is claimed blocked now that #311 and #312 have shipped; this
    # assertion used to require the opposite.
    assert "Blocked" not in page.text


def test_the_progress_view_needs_the_token_like_everything_else(
    client: tuple[TestClient, str],
) -> None:
    http, _token = client

    assert http.get("/ui/not-the-token/completeness").status_code == 404


def test_the_changelog_tab_links_each_pr(client: tuple[TestClient, str]) -> None:
    """Same reason as the text surface: a tenant reading the Changelog tab has
    no way to turn '#123' into anything (#357)."""
    http, token = client

    page = http.get(f"/ui/{token}/?tab=changelog").text

    assert 'href="https://github.com/dhk/wingman/pull/' in page
    assert 'rel="noopener noreferrer"' in page
