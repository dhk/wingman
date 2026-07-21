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
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.logs import get_logger

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

_UI_CSS = """
body { padding: 40px 24px; }
.ui { max-width: 720px; margin: 0 auto; }
.ui h2 { margin-top: 40px; }
.ui ul { list-style: none; padding-left: 0; }
.ui li { margin-bottom: 8px; }
.ui .when { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim);
  margin-right: 10px; }
.upload { background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--border-radius); padding: 20px; margin-top: 16px; }
.upload input[type=file] { margin: 8px 0 16px; display: block; }
.upload button { font-family: var(--font-cond); font-size: 16px; padding: 6px 20px;
  background: var(--accent); color: white; border: 0; border-radius: var(--border-radius);
  cursor: pointer; }
.report-box { background: var(--bg2); border-left: 3px solid var(--accent);
  padding: 12px 16px; margin: 16px 0; white-space: pre-wrap;
  font-family: var(--font-mono); font-size: 13px; }
.report-box.err { border-left-color: var(--accent-orange); }
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _page(title: str, body: str) -> HTMLResponse:
    from wingman.reporting.export import WINGMAN_PDF_CSS

    return HTMLResponse(
        "<!doctype html>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n"
        f"<style>\n{WINGMAN_PDF_CSS}\n{_UI_CSS}</style>\n"
        f'<div class="ui">\n{body}\n</div>\n'
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
        if path in htmls or path.name == "latest.md":
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


async def ui_home(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    body: list[str] = ["<h1>Wingman</h1>"]
    body.append(f'<div class="meta">workspace: {_e(str(config.data_dir))}</div>')

    latest = config.reports_dir / "digests" / "latest.html"
    if latest.exists():
        stamp = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
        body.append(
            f'<p><a href="file/digests/latest.html">Today&rsquo;s digest &rarr;</a> '
            f'<span class="when">{stamp.strftime("%Y-%m-%d %H:%M UTC")}</span></p>'
        )
    else:
        body.append('<p class="dim">No digest yet — the first overnight run writes one.</p>')

    grouped: dict[str, list[Path]] = {}
    for section, path in _listed_files(config.reports_dir):
        grouped.setdefault(section, [])
        if len(grouped[section]) < _MAX_LISTED_PER_SECTION:
            grouped[section].append(path)
    for section in sorted(grouped):
        body.append(f"<h2>{_e(section)}</h2><ul>")
        for path in grouped[section]:
            rel = path.relative_to(config.reports_dir).as_posix()
            when = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
            body.append(
                f'<li><span class="when">{when}</span>'
                f'<a href="file/{_e(rel)}">{_e(path.name)}</a></li>'
            )
        body.append("</ul>")

    body.append("<h2>Add to the workspace</h2>")
    body.append(
        '<div class="upload">'
        "<p>A LinkedIn data-export <b>.zip</b>, or a resume "
        "(<b>.md .txt .pdf .docx .tex</b>). It lands in the inbox and runs the "
        "ordinary ingest pipeline — nothing else is touched.</p>"
        '<form method="post" enctype="multipart/form-data" action="upload">'
        '<input type="file" name="file" required>'
        "<button>Upload &amp; ingest</button></form></div>"
    )

    body.append("<h2>API keys</h2>")
    states = {short: state for short, _var, state in key_status_rows(config.data_dir)}
    body.append(
        '<div class="upload">'
        f"<p>Each key is <b>verified against its provider</b> before it is stored "
        f"(workspace file, owner-only). A key set in the service environment always "
        f"wins (RFC-019). Current: anthropic — <b>{_e(states.get('anthropic', '?'))}</b>, "
        f"voyage — <b>{_e(states.get('voyage', '?'))}</b>.</p>"
        '<form method="post" action="keys">'
        '<label>Anthropic key <input type="password" name="anthropic" '
        'autocomplete="off"></label><br>'
        '<label>Voyage key <input type="password" name="voyage" '
        'autocomplete="off"></label><br><br>'
        "<button>Verify &amp; store</button></form></div>"
    )
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
    if not config.db_path.exists():
        return _page(
            "Upload",
            f'{back}<div class="report-box err">The workspace is not initialized — '
            "run 'wingman init' on the server first.</div>",
        )

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
