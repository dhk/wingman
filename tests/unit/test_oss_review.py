"""scripts/oss_review.py: batching, the deterministic finding gate, and a full run
against a fake OpenAI-compatible server (no network, no real model)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "oss_review.py"
_spec = importlib.util.spec_from_file_location("oss_review", _SCRIPT)
assert _spec and _spec.loader
oss = importlib.util.module_from_spec(_spec)
sys.modules["oss_review"] = oss
_spec.loader.exec_module(oss)


# --------------------------------------------------------------------------- batching


def test_split_file_cuts_at_top_level_definitions_within_budget() -> None:
    funcs = [f"def f{i}():\n" + "    x = 1\n" * 20 + "\n" for i in range(10)]
    text = "".join(funcs)
    units = oss.split_file("m.py", text, budget_chars=len(funcs[0]) * 3 + 10)
    assert len(units) > 1
    assert "".join(u.text for u in units) == text  # nothing lost, nothing duplicated
    for u in units:
        assert u.chars <= len(funcs[0]) * 3 + 10
        assert u.text.startswith("def f")  # every cut lands on a definition
    assert units[0].start == 1 and units[-1].end == text.count("\n")


def test_render_batch_numbers_lines_from_unit_start() -> None:
    u = oss.Unit("a.py", 41, 42, 99, "x = 1\ny = 2\n")
    rendered = oss.render_batch(oss.Batch(0, [u]), {"a.py": [(42, 42)]})
    assert "=== FILE: a.py (lines 41-42 of 99) ===" in rendered
    assert "41| x = 1" in rendered and "42| y = 2" in rendered
    assert "Changed in this pull request: lines 42-42" in rendered


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "reply",
    [
        '{"findings": []}',
        '<think>hmm {"no": 1}</think>\n{"findings": []}',
        'Here you go:\n```json\n{"findings": []}\n```',
    ],
)
def test_parse_findings_tolerates_reasoning_and_fences(reply: str) -> None:
    assert oss.parse_findings(reply) == []


def test_parse_findings_rejects_reply_without_findings() -> None:
    with pytest.raises(ValueError):
        oss.parse_findings("I could not review this.")
    with pytest.raises(ValueError):
        oss.parse_findings('{"verdict": "ok"}')


# --------------------------------------------------------------------------- the gate

_LINES = ["import os", "", "def run(cmd):", "    os.system(cmd)", "    return 0"]


def _finding(**kw: Any) -> dict[str, Any]:
    base = {
        "file": "a.py",
        "line": 4,
        "severity": "high",
        "category": "security",
        "title": "Shell injection",
        "detail": "cmd reaches a shell",
        "evidence": "os.system(cmd)",
        "suggestion": "use subprocess.run([...])",
        "confidence": 0.9,
    }
    return {**base, **kw}


def test_validate_verifies_relocates_and_flags() -> None:
    lines = [*_LINES, *["pass"] * 20]
    shown = {"a.py": [(1, len(lines))]}
    raw = [
        _finding(),  # evidence at the cited line
        _finding(line=2, title="off by two"),  # model miscounted slightly
        _finding(line=20, title="far off"),  # evidence exists, but far from line 20
        _finding(evidence="eval(user_input)", title="invented"),  # evidence nowhere
    ]
    kept, rejected = oss.validate(raw, shown, {"a.py": lines})
    assert rejected == []
    status = {f["title"]: (f["evidence_status"], f["line"]) for f in kept}
    assert status["Shell injection"] == ("verified", 4)
    assert status["off by two"] == ("verified", 4)
    assert status["far off"] == ("relocated", 4)
    assert status["invented"] == ("unverified", 4)


def test_validate_rejects_files_and_lines_the_model_was_not_shown() -> None:
    shown = {"a.py": [(3, 5)]}
    raw = [_finding(file="secret.py"), _finding(line=1), _finding(line="x")]
    kept, rejected = oss.validate(raw, shown, {"a.py": _LINES})
    assert kept == []
    assert [r["_reason"].split()[0] for r in rejected] == ["file", "line", "line"]


def test_validate_keeps_dotfile_paths_intact() -> None:
    # Regression: lstrip("./") would turn ".github/x.yml" into "github/x.yml".
    shown = {".github/x.yml": [(1, 1)]}
    kept, _ = oss.validate(
        [_finding(file="./.github/x.yml", line=1, evidence="on: push")],
        shown,
        {".github/x.yml": ["on: push"]},
    )
    assert kept and kept[0]["file"] == ".github/x.yml"


def test_validate_normalises_unknown_enums_and_clamps_confidence() -> None:
    kept, _ = oss.validate(
        [_finding(severity="BLOCKER", category="vibes", confidence=7)],
        {"a.py": [(1, 5)]},
        {"a.py": _LINES},
    )
    assert (kept[0]["severity"], kept[0]["category"], kept[0]["confidence"]) == (
        "medium",
        "other",
        1.0,
    )


def test_filter_to_changes_keeps_nearby_and_severe_only() -> None:
    fs = [
        {"file": "a.py", "line": 10, "severity": "low"},
        {"file": "a.py", "line": 90, "severity": "low"},
        {"file": "a.py", "line": 90, "severity": "high"},
    ]
    out = oss.filter_to_changes(fs, {"a.py": [(8, 12)]})
    assert [(f["line"], f["severity"]) for f in out] == [(10, "low"), (90, "high")]


# --------------------------------------------------------------------------- end to end


class _FakeModel:
    """Minimal /chat/completions + /models server with scripted behaviour."""

    def __init__(self, fail_first: int = 0, truncate_to: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_first = fail_first
        self.truncate_to = truncate_to  # report this many prompt tokens, like a cut context
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_GET(self) -> None:
                body = {
                    "data": [{"id": "fake/model", "pricing": {"input": "1e-6", "output": "2e-6"}}]
                }
                self._send(200, body)

            def do_POST(self) -> None:
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append(req)
                if len(outer.calls) <= outer.fail_first:
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.end_headers()
                    return
                user = req["messages"][1]["content"]
                findings = []
                if "os.system(cmd)" in user:
                    findings = [_finding(file="pkg/a.py", line=4)]
                    findings.append(_finding(file="../../etc/passwd", line=1, title="escape"))
                content = json.dumps({"findings": findings})
                chars = sum(len(m["content"]) for m in req["messages"])
                prompt_tokens = outer.truncate_to or chars // 4
                self._send(
                    200,
                    {
                        "model": "fake/model",
                        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 10},
                    },
                )

            def _send(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("\n".join(_LINES) + "\n")
    (tmp_path / "pkg" / "b.py").write_text("X = 1\n")
    (tmp_path / "uv.lock").write_text("lock\n")
    (tmp_path / "AGENTS.md").write_text("Rule: no shell=True.\n")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    yield tmp_path


def _run(repo: Path, fake: _FakeModel, *extra: str) -> int:
    base = ["run", "--root", str(repo), "--base-url", fake.url, "--model", "fake/model"]
    return int(oss.main([*base, "--allow-no-key", "--no-cross-cutting", *extra]))


def test_full_run_writes_report_and_reuses_cache(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OSS_REVIEW_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    fake = _FakeModel()
    assert _run(repo, fake) == 0
    assert len(fake.calls) == 1  # both files fit one batch; uv.lock is excluded
    sent = fake.calls[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert "Rule: no shell=True." in sent["messages"][0]["content"]  # AGENTS.md as data
    assert "uv.lock" not in sent["messages"][1]["content"]

    out = json.loads((repo / "reports/oss-review/findings.json").read_text())
    assert [f["evidence_status"] for f in out["findings"]] == ["verified"]
    assert out["rejected"][0]["_reason"] == "file not in the reviewed slice"
    prompt_tokens = out["manifest"]["usage"]["prompt_tokens"]
    assert prompt_tokens > 0 and out["manifest"]["usage"]["completion_tokens"] == 10
    assert out["manifest"]["estimated_cost_usd"] == pytest.approx(prompt_tokens * 1e-6 + 20e-6)
    assert out["manifest"]["batch_warnings"] == []
    report = (repo / "reports/oss-review/report.md").read_text()
    assert "## High (1)" in report and "pkg/a.py:4" in report

    # Unchanged code -> no second paid call.
    assert _run(repo, fake) == 0
    assert len(fake.calls) == 1
    again = json.loads((repo / "reports/oss-review/findings.json").read_text())
    assert again["manifest"]["batches_cached"] == 1


def test_run_retries_rate_limits(repo: Path) -> None:
    fake = _FakeModel(fail_first=2)
    assert _run(repo, fake) == 0
    assert len(fake.calls) == 3


def test_fail_on_threshold_sets_exit_code(repo: Path) -> None:
    assert _run(repo, _FakeModel(), "--fail-on", "high") == 1
    assert _run(repo, _FakeModel(), "--fail-on", "critical", "--no-cache") == 0


def test_diff_mode_reviews_only_changed_files(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feat"], check=True)
    (repo / "pkg" / "b.py").write_text("X = 1\nY = 2\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "b"], check=True)
    fake = _FakeModel()
    assert _run(repo, fake, "--diff-base", "main") == 0
    user = fake.calls[0]["messages"][1]["content"]
    assert "pkg/b.py" in user and "pkg/a.py" not in user
    assert "Changed in this pull request: lines 2-2" in user


def test_missing_key_sends_nothing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSS_REVIEW_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    fake = _FakeModel()
    with pytest.raises(SystemExit, match="Nothing was sent"):
        oss.main(["run", "--root", str(repo), "--base-url", fake.url])
    assert fake.calls == []


def test_max_cost_guard_refuses_before_calling(repo: Path) -> None:
    fake = _FakeModel()
    with pytest.raises(SystemExit, match="exceeds --max-cost"):
        _run(repo, fake, "--max-cost", "0.000001")
    assert fake.calls == []


def test_truncated_prompt_is_flagged_and_not_cached(repo: Path) -> None:
    fake = _FakeModel(truncate_to=50)  # e.g. Ollama's default 4k context on a big batch
    assert _run(repo, fake) == 0
    out = json.loads((repo / "reports/oss-review/findings.json").read_text())
    [warning] = out["manifest"]["batch_warnings"]
    assert "truncated" in warning["warning"]
    assert (
        "may not have been reviewed in full" in (repo / "reports/oss-review/report.md").read_text()
    )
    assert _run(repo, fake) == 0
    assert len(fake.calls) == 2  # not served from cache: the re-run asks again
