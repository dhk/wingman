"""The read surface (RFC-033): a small web UI riding the HTTP MCP server.

Conversation is for thinking; this page is for what conversation is bad
at — glancing and files. It serves what the workspace already renders
(digests, packs, dossiers, exports) behind the same capability-path
token as the MCP transport, plus one upload form for the two artifacts
that cannot travel through a chat: a LinkedIn export zip and a resume
file. Deliberately NOT here: search boxes, triage buttons, graph
browsing — the conversational client is that interface, and duplicating
it here is the road to a product this isn't (hosted tiers stay parked).

Security shape matches RFC-017: loopback bind, token in the path
(constant-time compared), reached through the user's own tunnel. File
serving is confined to the reports directory; uploads are size-capped,
land in the inbox like any other ingested artifact, and flow through
the ordinary deterministic pipelines.
"""

from __future__ import annotations

import html
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.logs import get_logger
from wingman.reporting.design_tokens import DESIGN_TOKENS_CSS

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_logger = get_logger("webui")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_RESUME_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx", ".tex"}
_SERVE_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".css": "text/css",
}
_MAX_LISTED_PER_SECTION = 12

# Component rules only — the token block is prepended below (issue #118 §9:
# one shared constant feeds this stylesheet and WINGMAN_PDF_CSS alike).
_UI_RULES_CSS = """
body { margin: 0; }
.ui { max-width: 720px; margin: 0 auto; padding: 24px 16px 64px;
  display: flex; flex-direction: column; gap: 24px; }
.hdr { display: flex; align-items: center; gap: 8px; padding-bottom: 12px;
  border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.hdr .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--accent); }
.hdr .wordmark { font-size: 18px; font-weight: 700; color: var(--text-head); }
.hdr .meta { margin: 0; flex-basis: 100%; }
.eyebrow { font-family: var(--font-mono); font-size: 11px; letter-spacing: .04em;
  text-transform: uppercase; color: var(--text-dim); }
.hero { display: flex; align-items: center; gap: 12px; border: 1px solid var(--accent);
  background: var(--bg2); border-radius: var(--border-radius); padding: 16px;
  min-height: 64px; text-decoration: none; color: var(--text);
  transition: background .15s, border-color .15s; }
.hero:hover { background: var(--bg3); }
.hero .hbody { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.hero .title { font-size: 20px; font-weight: 600; color: var(--text-head); }
.hero .sub { color: var(--text-muted); font-size: 14px; margin: 0; }
.hero .arrow { color: var(--accent); font-size: 20px; }
.group { display: flex; flex-direction: column; gap: 8px; }
.divider { display: flex; align-items: center; gap: 12px; }
.divider span { font-family: var(--font-mono); font-size: 10px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--text-dim); white-space: nowrap; }
.divider::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.rows { border: 1px solid var(--border); border-radius: var(--border-radius); overflow: hidden; }
.row { display: grid; grid-template-columns: 62px 1fr auto; gap: 12px; align-items: center;
  padding: 10px 12px; min-height: 44px; text-decoration: none; color: var(--text);
  border-top: 1px solid var(--border-light); transition: background .15s; }
.row:first-child { border-top: 0; }
.row:hover { background: var(--bg3); }
.row .when { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); }
.row .arrow { color: var(--text-dim); }
.chip { font-family: var(--font-mono); font-size: 10px; text-transform: uppercase;
  letter-spacing: .04em; padding: 2px 8px; border-radius: 999px; }
.chip-digest { background: rgba(43, 80, 232, .12); color: var(--accent); }
.chip-pack { background: rgba(107, 63, 212, .12); color: var(--accent-purple); }
.chip-dossier { background: rgba(14, 149, 145, .12); color: var(--teal); }
.chip-export { background: rgba(209, 102, 10, .12); color: var(--accent-orange); }
.panel { border: 1px solid var(--border); background: var(--bg2);
  border-radius: var(--border-radius); padding: 16px;
  display: flex; flex-direction: column; gap: 16px; }
.panel p { margin: 0; color: var(--text-muted); font-size: 14px; }
.stepno { font-family: var(--font-mono); font-size: 11px; color: var(--accent);
  letter-spacing: .04em; text-transform: uppercase; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field label { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; color: var(--text-dim); }
.field input { border: 1px solid var(--border); border-radius: var(--border-radius);
  background: var(--bg); color: var(--text); padding: 10px 12px; font: inherit; }
.field input[readonly] { color: var(--text-dim); background: var(--bg3); }
.status { display: flex; align-items: center; gap: 6px; font-family: var(--font-mono);
  font-size: 11px; color: var(--text-dim); }
.status .sdot { width: 7px; height: 7px; border-radius: 999px; background: var(--text-dim); }
.status.ok { color: var(--teal); }
.status.ok .sdot { background: var(--teal); }
.status.warn { color: var(--accent-orange); }
.status.warn .sdot { background: var(--accent-orange); }
.btn { font-family: var(--font-mono); font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; min-height: 44px; width: 100%; border: 0;
  border-radius: var(--border-radius); background: var(--accent); color: #fff;
  cursor: pointer; transition: background .15s; }
.btn:hover { background: var(--accent-hv); }
.report-box { border-left: 3px solid var(--teal); background: var(--bg2);
  border-radius: 0 var(--border-radius) var(--border-radius) 0; padding: 12px 16px;
  white-space: pre-wrap; font-family: var(--font-mono); font-size: 13px; }
.report-box.err { border-left-color: var(--accent-orange); }
.empty { border: 1px dashed var(--border); background: var(--bg2);
  border-radius: var(--border-radius); padding: 24px 16px; text-align: center;
  display: flex; flex-direction: column; gap: 4px; }
.empty b { color: var(--text-head); }
.manage-hd { font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: .04em; color: var(--text-dim);
  padding: 13px 0; }
.manage-body { display: flex; flex-direction: column; gap: 16px; }
.row-older { padding: 10px 12px; font-family: var(--font-mono); font-size: 11px;
  color: var(--text-dim); border-top: 1px solid var(--border-light); }
/* Desktop tabs (spec section 6): CSS-only — hidden radios + :checked siblings, no
   JavaScript. Narrow viewports never see the bar: every panel stacks in DOM
   order, so the phone page is the same content top to bottom. With CSS
   unavailable the radios and labels degrade to stacked labeled sections. */
.tabset { display: flex; flex-direction: column; gap: 24px; }
.tabset > input { position: absolute; width: 1px; height: 1px; opacity: 0;
  pointer-events: none; }
.tabbar { display: none; }
.tabpanel { display: flex; flex-direction: column; gap: 24px; }
@media (min-width: 768px) {
  .ui-setup { max-width: 560px; }
  .tabbar { display: flex; gap: 4px; border-bottom: 1px solid var(--border); }
  .tabbar label { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase;
    letter-spacing: .04em; color: var(--text-dim); padding: 0 14px; min-height: 44px;
    display: flex; align-items: center; cursor: pointer; margin-bottom: -1px;
    border-bottom: 2px solid transparent; transition: color .15s, border-color .15s; }
  .tabbar label:hover { color: var(--text); }
  .tabpanel { display: none; }
  #tab-digest:checked ~ .tabpanel-digest,
  #tab-files:checked ~ .tabpanel-files,
  #tab-connect:checked ~ .tabpanel-connect,
  #tab-manage:checked ~ .tabpanel-manage { display: flex; }
  #tab-digest:checked ~ .tabbar label[for="tab-digest"],
  #tab-files:checked ~ .tabbar label[for="tab-files"],
  #tab-connect:checked ~ .tabbar label[for="tab-connect"],
  #tab-manage:checked ~ .tabbar label[for="tab-manage"] {
    color: var(--text); border-bottom-color: var(--accent); }
}
"""

_UI_CSS = DESIGN_TOKENS_CSS + _UI_RULES_CSS


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _page(title: str, body: str, shell: str = "ui") -> HTMLResponse:
    # Tokens ride _UI_CSS (shared block, emitted once); the export stylesheet
    # contributes only its component rules so nothing is defined twice.
    from wingman.reporting.export import WINGMAN_PDF_RULES_CSS

    return HTMLResponse(
        "<!doctype html>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n"
        f"<style>\n{_UI_CSS}\n{WINGMAN_PDF_RULES_CSS}</style>\n"
        f'<div class="{shell}">\n{body}\n</div>\n'
    )


def _authorized(request: Request) -> Config | None:
    """The path token is the credential (RFC-017 shape); wrong or absent -> None."""
    config = load_config()
    token_path = config.data_dir / "mcp-http-token"
    if not token_path.exists():
        return None
    expected = token_path.read_text(encoding="utf-8").strip()
    presented = str(request.path_params.get("token", ""))
    if not secrets.compare_digest(presented, expected):
        return None
    return config


def _not_found() -> Response:
    # Wrong token and wrong path look identical: no oracle for token guessing.
    return Response("not found", status_code=404)


def _listed_files(root: Path) -> list[tuple[str, Path]]:
    """(section, file) pairs worth linking, newest first within each section."""
    if not root.exists():
        return []
    sections: list[tuple[str, Path]] = []
    candidates = [path for path in root.rglob("*") if path.suffix in _SERVE_TYPES]
    # A digest's .md twin duplicates its .html: prefer the pretty one.
    htmls = {path.with_suffix(".md") for path in candidates if path.suffix == ".html"}
    for path in candidates:
        if path in htmls or path.name in ("latest.md", "latest.html"):
            continue
        section = path.parent.relative_to(root).as_posix()
        sections.append(("reports" if section == "." else section, path))
    sections.sort(key=lambda entry: entry[1].stat().st_mtime, reverse=True)
    return sections


async def ui_home_redirect(request: Request) -> Response:
    """Canonicalize to the trailing-slash landing with a RELATIVE Location.

    Every URL this UI emits is relative, so it works unchanged behind a
    path-mounting reverse proxy (e.g. 'tailscale serve --set-path /trent'),
    which strips the mount prefix before forwarding: the server never needs
    to know its public prefix. That only holds if the landing URL ends in
    '/', and only a relative redirect ('<token>/', resolved by the browser
    against the public URL) preserves the prefix the server can't see.
    """
    if _authorized(request) is None:
        return _not_found()
    return RedirectResponse(url=f"{request.path_params['token']}/", status_code=307)


# Directory -> humanized group name (issue #95: never raw directory names).
_GROUP_NAMES = {
    "digests": "Overnight digests",
    "packs": "Application packs",
    "dossiers": "Company dossiers",
    "pdf": "Exports",
    "reports": "Reports",
}
_STAMP = re.compile(r"(20\d{6})T(\d{2})(\d{2})\d*Z?")
_ISO_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def _humanize(path: Path, now: datetime) -> tuple[str, str]:
    """(date label, title) for an artifact — filenames never reach the page."""
    stem = path.stem
    label = ""
    stamped = _STAMP.search(stem)
    if stamped:
        when = datetime.strptime(stamped.group(1), "%Y%m%d").replace(tzinfo=UTC)
        hm = f"{stamped.group(2)}:{stamped.group(3)}"
        label = f"Today · {hm}" if when.date() == now.date() else when.strftime("%d %b")
        stem = _STAMP.sub("", stem)
    else:
        dated = _ISO_DATE.search(stem)
        if dated:
            when = datetime.strptime(dated.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
            label = "Today" if when.date() == now.date() else when.strftime("%d %b")
            stem = _ISO_DATE.sub("", stem)
    words = [w for w in re.split(r"[-_.]+", stem) if w]
    if words and words[0].lower() == "overnight":
        title = "Overnight digest"
    elif words and words[0].lower() == "pack":
        rest = " ".join(w.capitalize() for w in words[1:])
        title = rest or "Application pack"
    else:
        title = " ".join(w.capitalize() for w in words) or path.name
    return label, title


def _group_name(section: str) -> str:
    return _GROUP_NAMES.get(section, section.replace("-", " ").replace("_", " ").title())


def _header(config: Config) -> str:
    return (
        '<div class="hdr"><span class="dot"></span>'
        '<span class="wordmark">Wingman</span>'
        f'<div class="meta">{_e(str(config.data_dir))}</div></div>'
    )


def _artifact_sections(config: Config, now: datetime) -> list[tuple[str, str]]:
    """(section, rendered group) pairs \u2014 newest first, capped at 12 rows each."""
    grouped: dict[str, list[Path]] = {}
    for section, path in _listed_files(config.reports_dir):
        grouped.setdefault(section, []).append(path)
    parts: list[tuple[str, str]] = []
    for section in sorted(grouped):
        rows = []
        for path in grouped[section][:_MAX_LISTED_PER_SECTION]:
            rel = path.relative_to(config.reports_dir).as_posix()
            label, title = _humanize(path, now)
            rows.append(
                f'<a class="row" href="file/{_e(rel)}"><span class="when">{_e(label)}</span>'
                f"<span>{_e(title)}</span>" + '<span class="arrow">\u2192</span></a>'
            )
        if len(grouped[section]) > _MAX_LISTED_PER_SECTION:
            rows.append('<div class="row-older">older\u2026</div>')
        parts.append(
            (
                section,
                f'<div class="group"><div class="divider"><span>{_e(_group_name(section))}</span>'
                '</div><div class="rows">' + "".join(rows) + "</div></div>",
            )
        )
    return parts


def _tabset(panels: list[tuple[str, str, str]]) -> str:
    """Desktop tabs (spec section 6): radios first, then labels, then panels.

    CSS-only \u2014 hidden radio inputs drive :checked sibling selectors; the page
    carries no JavaScript. Below 768px the bar is hidden and every panel
    stacks in DOM order, so narrow viewports read the same single column as
    before; with CSS unavailable the whole thing degrades to stacked labeled
    sections. Empty panels are dropped (no dead tab), and a lone panel needs
    no chrome at all.
    """
    filled = [(key, label, content) for key, label, content in panels if content]
    if not filled:
        return ""
    if len(filled) == 1:
        return filled[0][2]
    inputs = "".join(
        f'<input type="radio" name="view" id="tab-{key}"{" checked" if index == 0 else ""}>'
        for index, (key, _, _) in enumerate(filled)
    )
    labels = "".join(f'<label for="tab-{key}">{_e(label)}</label>' for key, label, _ in filled)
    sections = "\n".join(
        f'<div class="tabpanel tabpanel-{key}">\n{content}\n</div>' for key, _, content in filled
    )
    return f'<div class="tabset">\n{inputs}\n<nav class="tabbar">{labels}</nav>\n{sections}\n</div>'


def _tiers(groups: list[tuple[str, str]], manage: str, connect: str) -> str:
    """Tier 2 + Tier 3 as Digest · Files · Connect · Manage (tabs at >=768px, a stack below)."""
    digest = "\n".join(html for section, html in groups if section.split("/")[0] == "digests")
    files = "\n".join(html for section, html in groups if section.split("/")[0] != "digests")
    return _tabset(
        [
            ("digest", "Digest", digest),
            ("files", "Files", files),
            ("connect", "Connect", connect),
            ("manage", "Manage", manage),
        ]
    )


def _upload_panel(step: str = "") -> str:
    lead = f'<span class="stepno">{_e(step)}</span>' if step else ""
    return (
        f'<div class="panel">{lead}'
        "<p>A LinkedIn data-export <b>.zip</b>, or a resume "
        "(<b>.md .txt .pdf .docx .tex</b>). It lands in the inbox and runs the "
        "ordinary ingest pipeline — nothing else is touched.</p>"
        '<form method="post" enctype="multipart/form-data" action="upload" class="field">'
        '<label>File</label><input type="file" name="file" required>'
        '<button class="btn">Upload &amp; ingest</button></form></div>'
    )


def _own_prefix(request: Request, token: str) -> str:
    """The mount prefix this request actually arrived under ('' or '/trent').

    Read back from the matched path rather than threaded through as state,
    so it is always the prefix this instance is really mounted at — the
    same value 'register_ui' was given, computed with zero extra plumbing.
    """
    suffix = f"/ui/{token}/"
    path = request.url.path
    return path[: -len(suffix)] if path.endswith(suffix) else ""


def _url_field(label: str, value: str) -> str:
    return (
        f'<div class="field"><label>{_e(label)}</label>'
        f'<input type="text" value="{_e(value)}" readonly></div>'
    )


def _connect_panel(request: Request, token: str, step: str = "") -> str:
    """Same URLs 'wingman mcp url' prints, rendered here so reaching them
    never requires shell access to the host — the point for a friend
    running someone else's box (MULTI-INSTANCE-DESIGN.md shape B).
    """
    from wingman.mcp_server import (
        _extra_allowed_hosts,
        _tunnel_port,
        connector_urls,
        server as mcp_server,
    )

    lead = (
        f'<span class="stepno">{_e(step)}</span>'
        if step
        else '<span class="stepno">Connect a client</span>'
    )
    host = getattr(mcp_server.settings, "host", "127.0.0.1")
    port = getattr(mcp_server.settings, "port", 8787)
    prefix = _own_prefix(request, token)
    extra_hosts = _extra_allowed_hosts(None)
    fields = "".join(
        _url_field(label, url)
        for label, url in connector_urls(
            token, extra_hosts, host=host, port=port, prefix=prefix, tunnel_port=_tunnel_port()
        )
    )
    hint = ""
    if not extra_hosts:
        hint = (
            '<p class="dim">No tunnel hostname detected — only reachable from this '
            "machine right now. Run <code>tailscale serve</code> (your own devices) or "
            "<code>tailscale funnel</code> (claude.ai web/mobile) to reach it elsewhere.</p>"
        )
    return (
        f'<div class="panel">{lead}'
        "<p>Paste the MCP connector URL into claude.ai → Settings → Connectors "
        "→ Add custom connector — the same URL works in Claude Desktop and the "
        "mobile apps. Treat both URLs like a password: anyone holding one can read and "
        "upload to this workspace.</p>"
        f"{hint}{fields}</div>"
    )


def _key_field(short: str, env_var: str, source: str) -> str:
    label = f"{short.capitalize()} key"
    if source == "environment":
        return (
            f'<div class="field"><label>{_e(label)}</label>'
            '<input type="password" value="********" readonly>'
            f'<span class="status warn"><span class="sdot"></span>'
            f"shadowed \u2014 {_e(env_var)} in the service environment wins</span></div>"
        )
    if source == "workspace file":
        status = (
            '<span class="status ok"><span class="sdot"></span>verified · workspace file</span>'
        )
    else:
        status = '<span class="status"><span class="sdot"></span>not set</span>'
    return (
        f'<div class="field"><label>{_e(label)}</label>'
        f'<input type="password" name="{_e(short)}" autocomplete="off">'
        f"{status}</div>"
    )


def _keys_panel(config: Config, step: str = "") -> str:
    lead = (
        f'<span class="stepno">{_e(step)}</span>'
        if step
        else '<span class="stepno">API keys</span>'
    )
    fields = "".join(
        _key_field(short, env_var, source)
        for short, env_var, source in key_status_rows(config.data_dir)
    )
    return (
        f'<div class="panel">{lead}'
        "<p>Each key is <b>verified against its provider</b> before it is stored "
        "(workspace file, owner-only). A key set in the service environment always "
        "wins (RFC-019).</p>"
        f'<form method="post" action="keys" class="field">{fields}'
        '<button class="btn">Verify &amp; store</button></form></div>'
    )


async def ui_home(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    now = datetime.now(UTC)
    has_workspace = config.db_path.exists()
    latest = config.reports_dir / "digests" / "latest.html"
    token = str(request.path_params["token"])
    body: list[str] = [_header(config)]

    if not has_workspace:
        # State 2 — fresh: guided setup, nothing dead above the fold.
        body.append("<h1>Set up your workspace</h1>")
        body.append(
            '<p class="dim">Two steps and Wingman is yours: a key it can spend, '
            "and your data to reason over.</p>"
        )
        body.append(_keys_panel(config, step="01 \u2014 Add an API key"))
        body.append(_upload_panel(step="02 \u2014 Upload your data"))
        body.append(_connect_panel(request, token, step="03 \u2014 Connect a Claude client"))
        # No tabs here (nothing to tab yet) \u2014 desktop just centers the setup.
        return _page("Wingman \u2014 setup", "\n".join(body), shell="ui ui-setup")

    if latest.exists():
        # State 1 — daily: the digest is the fold.
        stamp = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
        eyebrow = (
            f"Today · {stamp.strftime('%H:%M')} UTC"
            if stamp.date() == now.date()
            else stamp.strftime("%d %b · %H:%M UTC")
        )
        body.append(
            '<a class="hero" href="file/digests/latest.html"><div class="hbody">'
            f'<span class="eyebrow">{_e(eyebrow)}</span>'
            '<span class="title">Overnight digest</span>'
            '<p class="sub">Actions first, then everything that changed.</p>'
            "</div>" + '<span class="arrow">\u2192</span></a>'
        )
        manage = (
            '<div class="manage"><div class="manage-hd">Manage \u2014 keys &amp; uploads</div>'
            '<div class="manage-body">' + _upload_panel() + _keys_panel(config) + "</div></div>"
        )
        body.append(_tiers(_artifact_sections(config, now), manage, _connect_panel(request, token)))
        return _page("Wingman", "\n".join(body))

    # State 3 — degraded: workspace lives, no digest yet.
    body.append(
        '<div class="empty"><span class="eyebrow" style="color: var(--accent-orange)">'
        "No digest yet</span><b>The first overnight run writes one.</b>"
        '<span class="dim">Run <code>wingman overnight</code> \u2014 or follow a company first.</span></div>'
    )
    manage = (
        '<div class="manage"><div class="manage-hd">Manage \u2014 keys &amp; uploads</div>'
        '<div class="manage-body">' + _upload_panel() + _keys_panel(config) + "</div></div>"
    )
    body.append(_tiers(_artifact_sections(config, now), manage, _connect_panel(request, token)))
    return _page("Wingman", "\n".join(body))


async def ui_file(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    root = config.reports_dir.resolve()
    target = (root / str(request.path_params["path"])).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return _not_found()
    media = _SERVE_TYPES.get(target.suffix)
    if media is None:
        return _not_found()
    return FileResponse(target, media_type=media)


async def ui_upload(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    back = '<p><a href="./">&larr; back</a></p>'

    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return _page("Upload", f'{back}<div class="report-box err">No file was sent.</div>')
    data = await upload.read()
    name = Path(upload.filename or "upload").name
    suffix = Path(name).suffix.lower()
    if len(data) > MAX_UPLOAD_BYTES:
        return _page(
            "Upload",
            f'{back}<div class="report-box err">{_e(name)} is over the '
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.</div>",
        )
    if suffix != ".zip" and suffix not in _RESUME_SUFFIXES:
        return _page(
            "Upload",
            f'{back}<div class="report-box err">Unsupported type {_e(suffix or name)}: '
            "send a LinkedIn export .zip or a resume (.md .txt .pdf .docx .tex).</div>",
        )
    # A fresh workspace initializes on first upload (issue #95: the setup
    # state must never present a dead control; Storage creates the schema).
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    stored = config.inbox_dir / f"{stamp}-{name}"
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(data)
    _logger.info("webui upload name=%s bytes=%d", name, len(data))

    from wingman.application.ingest import IngestError, ingest_resume
    from wingman.application.linkedin import import_linkedin
    from wingman.infrastructure.storage import Storage
    from wingman.providers.base import CapabilityClass, ProviderError
    from wingman.providers.router import ModelConfigError, get_provider

    try:
        with Storage(config.db_path) as storage:
            if suffix == ".zip":
                report = import_linkedin(stored, config, storage)
                summary = (
                    f"LinkedIn export imported: {report.positions} positions, "
                    f"{report.skills} skills, {report.recommendations} recommendations.\n"
                    f"Accepted: {report.counts.accepted}  Updated: {report.counts.updated}  "
                    f"Retired: {report.counts.retired}  Conflicts: {report.counts.conflicts}"
                )
            else:
                ingested = ingest_resume(
                    stored, config, storage, get_provider(CapabilityClass.EXTRACT_FAST, config)
                )
                rejected = "".join(
                    f"\n  rejected {item.name!r}: {item.reason}" for item in ingested.rejected
                )
                summary = (
                    f"Resume ingested. Accepted: {ingested.accepted}  "
                    f"Updated: {ingested.updated}  Retired: {ingested.retired}  "
                    f"Conflicts: {ingested.conflicts}  "
                    f"Rejected: {len(ingested.rejected)}{rejected}"
                )
    except (IngestError, ModelConfigError, ProviderError) as exc:
        return _page(
            "Upload", f'{back}<div class="report-box err">Ingestion failed: {_e(str(exc))}</div>'
        )
    return _page("Upload", f'{back}<div class="report-box">{_e(summary)}</div>')


def key_status_rows(data_dir: Path) -> list[tuple[str, str, str]]:
    """(short, env var, source) per key: environment / workspace file / not set."""
    from wingman.infrastructure.keys import KNOWN_KEYS, read_workspace_keys
    import os

    stored = read_workspace_keys(data_dir)
    rows: list[tuple[str, str, str]] = []
    for short, env_var in KNOWN_KEYS.items():
        if os.environ.get(env_var, "").strip():
            source = "environment"
        elif env_var in stored:
            source = "workspace file"
        else:
            source = "not set"
        rows.append((short, env_var, source))
    return rows


def _validate_anthropic(value: str) -> str | None:
    """A free, authenticated call: models.list succeeds only for a live key."""
    import anthropic

    try:
        anthropic.Anthropic(api_key=value).models.list()
    except anthropic.AuthenticationError:
        return "Anthropic rejected the key (authentication failed)."
    except Exception as exc:  # noqa: BLE001 — network shape varies; report, don't store
        return f"could not verify the Anthropic key ({exc})."
    return None


def _validate_voyage(value: str) -> str | None:
    """One tiny embed against the real endpoint; 401/403 means a bad key."""
    import httpx

    try:
        response = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {value}"},
            json={"input": ["ok"], "model": "voyage-3-lite"},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return f"could not verify the Voyage key ({exc})."
    if response.status_code in (401, 403):
        return "Voyage rejected the key (authentication failed)."
    if response.status_code >= 400:
        return f"Voyage verification failed (HTTP {response.status_code})."
    return None


# Injectable for tests: short name -> validator(value) -> error | None.
VALIDATORS = {"anthropic": _validate_anthropic, "voyage": _validate_voyage}


async def ui_keys(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    back = '<p><a href="./">&larr; back</a></p>'
    from wingman.infrastructure.keys import KeyStoreError, store_workspace_key

    form = await request.form()
    lines: list[str] = []
    failures: list[str] = []
    for short in ("anthropic", "voyage"):
        raw = form.get(short)
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            continue
        error = VALIDATORS[short](value)
        if error is not None:
            failures.append(f"{short}: {error} Nothing was stored for it.")
            continue
        try:
            live = store_workspace_key(config.data_dir, short, value)
        except KeyStoreError as exc:
            failures.append(f"{short}: {exc}")
            continue
        state = (
            "verified and live now"
            if live
            else "verified and stored — the service environment variable wins until it changes"
        )
        lines.append(f"{short}: {state}.")
        _logger.info("webui key stored short=%s live=%s", short, live)  # never the value
    if not lines and not failures:
        return _page("Keys", f'{back}<div class="report-box err">No key was entered.</div>')
    blocks = ""
    if lines:
        blocks += f'<div class="report-box">{_e(chr(10).join(lines))}</div>'
    if failures:
        blocks += f'<div class="report-box err">{_e(chr(10).join(failures))}</div>'
    return _page("Keys", back + blocks)


_registered_prefixes: set[str] = set()

# Captured once at import time (~= process start): the admin installations
# page (#130) polls this to show "running since" without needing any
# cross-workspace filesystem access into another instance's own state.
_STARTED_AT = datetime.now(UTC).isoformat(timespec="seconds")


async def ui_health(request: Request) -> Response:
    """Deliberately unauthenticated: version + start time only, nothing
    workspace-specific (no owner, no path, no data) — loopback-bound like
    the rest of this server, so the exposure is the same as 'ps' already
    gives anyone on the box. Lets the admin installations page (#130) tell
    an instance is up without holding its capability token.
    """
    from wingman.version import wingman_version

    return JSONResponse({"version": wingman_version(), "started_at": _STARTED_AT})


def normalize_prefix(prefix: str) -> str:
    """'' stays root; 'trent', '/trent', '/trent/' all become '/trent'."""
    cleaned = prefix.strip().strip("/")
    return f"/{cleaned}" if cleaned else ""


def register_ui(server: "FastMCP", prefix: str = "") -> None:
    """Mount the read surface, optionally under a native path prefix.

    With prefix='/trent' the server itself listens on /trent/ui/… — for
    pass-through fronts (nginx, Caddy, direct tailnet access) that forward
    the full path. A stripping proxy (tailscale serve --set-path) must NOT
    be combined with a prefix: it removes the mount segment before
    forwarding, and the two prefixes would stack. Pages need no awareness
    either way — every URL they emit is relative. Idempotent per prefix.
    """
    mount = normalize_prefix(prefix)
    if mount in _registered_prefixes:
        return
    _registered_prefixes.add(mount)
    server.custom_route(f"{mount}/ui/{{token}}", methods=["GET"])(ui_home_redirect)
    server.custom_route(f"{mount}/ui/{{token}}/", methods=["GET"])(ui_home)
    server.custom_route(f"{mount}/ui/{{token}}/file/{{path:path}}", methods=["GET"])(ui_file)
    server.custom_route(f"{mount}/ui/{{token}}/upload", methods=["POST"])(ui_upload)
    server.custom_route(f"{mount}/ui/{{token}}/keys", methods=["POST"])(ui_keys)
    server.custom_route(f"{mount}/health", methods=["GET"])(ui_health)
