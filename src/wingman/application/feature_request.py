"""Feature requests: from a conversational "feature request: …" to a GitHub issue (RFC-025).

Wingman's first gated external write. The flow is conversational — the
user says "feature request: <idea>", the model asks whatever clarifying
questions the idea needs, composes a title and body, and shows the exact
issue for confirmation — and the gate is structural, not behavioral: the
filing function refuses to run until confirmation is explicit, and the CLI
previews and prompts unless --yes. Filing uses the user's own `gh` CLI and
its auth, into a repo the user configured once; Wingman holds no GitHub
credential and still never sends outreach on anyone's behalf.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.telemetry import record_event

_logger = get_logger("application.feature_request")

_REPO_FILE = "feature-repo"
FEATURE_LABEL = "feature-request"

# (argv) -> (returncode, stdout, stderr); injectable so tests never run gh.
Runner = Callable[[list[str]], tuple[int, str, str]]


def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(  # noqa: S603 — fixed binary, no shell
        argv, capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout, result.stderr


class FiledIssue(BaseModel):
    repo: str
    title: str
    url: str


def get_feature_repo(config: Config) -> str | None:
    path = config.data_dir / _REPO_FILE
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def set_feature_repo(config: Config, repo: str) -> str:
    repo = repo.strip()
    if repo.count("/") != 1 or not all(part.strip() for part in repo.split("/")):
        raise IngestError(f"repo must look like owner/name; got {repo!r}.")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    (config.data_dir / _REPO_FILE).write_text(repo + "\n", encoding="utf-8")
    return repo


def render_preview(repo: str | None, title: str, body: str) -> str:
    """The exact issue, shown before anything leaves the machine."""
    return "\n".join(
        [
            f"Repo:  {repo or '(not set — wingman feature repo <owner/name>)'}",
            f"Title: {title}",
            f"Label: {FEATURE_LABEL}",
            "Body:",
            body.rstrip() or "(empty)",
        ]
    )


def file_feature_request(
    config: Config,
    title: str,
    body: str,
    runner: Runner | None = None,
) -> FiledIssue:
    """Create the issue via the user's own gh CLI. Call only after the user
    has seen the preview and explicitly confirmed (RFC-006/025)."""
    title = " ".join(title.split())
    if not title:
        raise IngestError("the feature request needs a title; nothing was filed.")
    repo = get_feature_repo(config)
    if repo is None:
        raise IngestError(
            "no feature-request repo configured. Set it once with "
            "'wingman feature repo <owner/name>'. Nothing was filed."
        )
    run = runner if runner is not None else _default_runner
    argv = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body",
        body,
        "--label",
        FEATURE_LABEL,
    ]
    try:
        code, out, err = run(argv)
    except FileNotFoundError as exc:
        raise IngestError(
            "the GitHub CLI ('gh') is not installed or not on PATH — "
            "install it and 'gh auth login', then retry. Nothing was filed."
        ) from exc
    if code != 0:
        detail = (err or out).strip().splitlines()
        raise IngestError(
            f"gh could not create the issue ({detail[-1] if detail else f'exit {code}'}). "
            "Nothing was filed. If the label is missing, create it once: "
            f"gh label create {FEATURE_LABEL} --repo {repo}"
        )
    url = out.strip().splitlines()[-1] if out.strip() else "(no url returned)"
    record_event(
        config,
        "cli",
        "feature-request-filed",
        {"repo": repo, "title": title, "url": url},
    )
    _logger.info("feature_request repo=%s title=%s url=%s", repo, title, url)
    return FiledIssue(repo=repo, title=title, url=url)
