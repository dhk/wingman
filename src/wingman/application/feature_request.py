"""Feature requests: from a conversational "feature request: …" to a GitHub issue (RFC-025).

Wingman's first gated external write. The flow is conversational — the
user says "feature request: <idea>", the model asks whatever clarifying
questions the idea needs, composes a title and body, and shows the exact
issue for confirmation — and the gate is structural, not behavioral: the
filing function refuses to run until confirmation is explicit, and the CLI
previews and prompts unless --yes. Filing uses `gh`, authenticated either
by whatever the invoking account already has set up (its own `gh auth
login`), or by GITHUB_SHARED_ISSUES_KEY (issue #205) when an account shares a
single fine-grained PAT rather than holding its own GitHub identity — in
that case GitHub's own "opened by" field can no longer say who actually
submitted it, so 'stamp_operator' appends a WINGMAN_OPERATOR_NAME line to
the body instead, before the issue is ever previewed or filed.

GITHUB_SHARED_ISSUES_KEY deliberately does NOT resolve the way the three
provider keys do (#506). Those are metered, so a tenant without one must
fail loud rather than spend the operator's money; this one is access to a
repo every tenant is already pointed at by construction, and RFC-047
specifies it as "one fine-grained PAT ... shared by every account".
Giving it the metered keys' isolation stopped every tenant from filing at
all. See _resolve_github_key for the ladder that replaced it, and why
"the operator's declared files" and "the process environment" have to be
different answers under a shared process.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.infrastructure import host_config, keys
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.telemetry import record_event

_logger = get_logger("application.feature_request")

_REPO_FILE = "feature-repo"
FEATURE_LABEL = "feature-request"

# (argv) -> (returncode, stdout, stderr); injectable so tests never run gh.
Runner = Callable[[list[str]], tuple[int, str, str]]


def _resolve_github_key(
    config: Config, home: Path | None = None, global_path: Path | None = None
) -> str | None:
    """The credential _default_runner translates to GH_TOKEN.

    The ladder: this workspace's own key (BYOK) first, then the operator's
    DECLARED file tiers, then nothing. Under strict_provider_keys the
    ambient process environment is still refused — and that distinction is
    the whole fix (#506).

    'ensure_env' hydrates the keychain, host, and global tiers INTO
    os.environ, so by the time anything reads the env it can no longer tell
    a credential the operator provisioned box-wide from whatever the
    account that happens to run a shared process exported. RFC-046's
    objection is only true of the ambient case, but with the tiers
    flattened together the only way to refuse ambient env was to refuse the
    operator's declared files along with it — which is what left every
    tenant unable to file. Reading the files directly keeps RFC-048's
    guarantee exactly (a tenant still cannot inherit the launching
    account's environment) while restoring RFC-047's shared credential.

    strict_provider_keys still governs the three metered keys: it is
    load-bearing for billing isolation and is not weakened here. Those
    keys reach the same declared tiers only for a tenant the operator has
    explicitly marked 'funded' (#514) — see providers.router.metered_key,
    which makes the same distinction this function does, for money instead
    of access.
    """
    if config.github_shared_issues_key is not None:
        return config.github_shared_issues_key
    env_var = keys.KNOWN_KEYS["github"]
    if config.strict_provider_keys:
        return keys.declared_shared_key(env_var, home, global_path)
    return keys.env_key(env_var) or keys.declared_shared_key(env_var, home, global_path)


def _default_runner(argv: list[str], github_key: str | None = None) -> tuple[int, str, str]:
    # GITHUB_SHARED_ISSUES_KEY is wingman's own name for this credential; gh
    # itself only recognizes GH_TOKEN/GITHUB_TOKEN natively, so translate it
    # here rather than making every caller know both names. When absent, gh
    # falls back to whatever it already had configured (e.g. 'gh auth
    # login') — unchanged from before this existed.
    env = dict(os.environ)
    if github_key:
        env["GH_TOKEN"] = github_key
    result = subprocess.run(  # noqa: S603 — fixed binary, no shell
        argv, capture_output=True, text=True, check=False, env=env
    )
    return result.returncode, result.stdout, result.stderr


def stamp_operator(body: str, home: Path | None = None, config: Config | None = None) -> str:
    """Append 'Submitted by: <name>', unchanged when there is no name to
    stamp (an account with its own GitHub identity has no need for this —
    GitHub's own 'opened by' field already answers it). Callers stamp ONCE
    and pass the same body to both render_preview and file_feature_request,
    so what's previewed is exactly what gets filed.

    'config.operator_name' wins over the WINGMAN_OPERATOR_NAME host setting
    because the host setting is one file per BOX (#506): under a shared
    multi-tenant process every tenant reads the same value, so the moment
    one shared PAT makes GitHub's 'opened by' say the operator for
    everybody, the stamp meant to recover who actually asked would say the
    same thing for everybody too. The tenant registry sets this per entry."""
    name = (config.operator_name if config is not None else None) or host_config.operator_name(home)
    if not name:
        return body
    separator = "\n\n" if body.strip() else ""
    return f"{body.rstrip()}{separator}Submitted by: {name}"


class FiledIssue(BaseModel):
    repo: str
    title: str
    url: str


def get_feature_repo(config: Config) -> str | None:
    """Where a feature request from this workspace goes.

    Resolved rather than asked (#371). A hosted tenant has no terminal —
    WALKTHROUGH-HOSTED opens by promising there is not one anywhere in it —
    so the per-workspace file below, written by `wingman feature repo`, is
    unreachable for exactly the people most likely to have a sharp request.
    Every tenant preview read "(not set)", which made wingman's one
    external write unusable for them.

    Nobody is ever shown a repository chooser. Somebody reporting that a
    button is confusing should not be handed a menu of repositories, so the
    operator configures this once and it is invisible thereafter.

    Order, most specific first:

    1. The tenant's own registry entry, for the rare case one tenant's
       requests belong somewhere else.
    2. The per-workspace file — somebody who ran `wingman feature repo`
       chose explicitly, and an explicit choice outranks any default
       below. A tenant has no such file, so this step is invisible to
       them and the defaults below are what they get; an unreadable one
       is skipped rather than raised, same as step 4.
    3. The registry's '[defaults] feature_repo' — one destination for
       every tenant the shared process serves, set once by the operator
       in the same file that lists them.
    4. WINGMAN_FEATURE_REPO in the host settings file (RFC-046) — the
       same box-wide default for accounts that aren't tenants at all
       (a solo shape-B install reads this and no registry).

    Both defaults exist because they cover different populations: the
    registry is the only file a hosted tenant is listed in, and the host
    settings file belongs to whichever account runs the process — under a
    shared process that account is the service account, not any tenant,
    so a tenant's destination cannot live there alone.

    Unset stays unset and is reported honestly, as before.
    """
    if config.feature_repo:
        return config.feature_repo
    workspace = _workspace_feature_repo(config)
    if workspace:
        return workspace
    if config.default_feature_repo:
        return config.default_feature_repo
    return _host_feature_repo()


def _workspace_feature_repo(config: Config) -> str | None:
    """The workspace's own `wingman feature repo` choice, or None.

    An unreadable workspace is treated as absent rather than fatal, the
    same posture _host_feature_repo takes below. This step is a *more
    specific* answer than the defaults under it, so failing to read it
    should cost the caller that specificity and nothing else — raising
    here would take out a tenant's feature request entirely, in favour of
    a file they never wrote and cannot write, when a perfectly good
    box-wide default was sitting one rung down.
    """
    try:
        path = config.data_dir / _REPO_FILE
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        _logger.warning(
            "cannot read %s (%s) — falling back to the configured default",
            config.data_dir / _REPO_FILE,
            exc,
        )
    return None


def _host_feature_repo() -> str | None:
    """WINGMAN_FEATURE_REPO from the host settings file, or None.

    An unreadable host file is treated as absent rather than fatal, the
    same posture the keys ladder takes: a box with a malformed wingman.env
    should still be able to report "no repo set" instead of failing the
    tool outright.
    """
    from wingman.infrastructure.host_config import HostEnvironmentError, read_host_settings

    try:
        return read_host_settings().get("WINGMAN_FEATURE_REPO", "").strip() or None
    except (HostEnvironmentError, OSError):
        return None


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
    if runner is not None:
        run = runner
    else:
        github_key = _resolve_github_key(config)

        def run(argv: list[str]) -> tuple[int, str, str]:
            return _default_runner(argv, github_key=github_key)

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
