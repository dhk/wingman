"""The agent loop's verdict validator (docs/AGENT-LOOP.md, RFC-083).

`.github/agent-loop/validate_verdict.py` is the deterministic boundary between a
reviewer model and the loop: a valid answer decides the round, an invalid one
hands the round to the next reviewer. So both directions matter. Usable answers
must survive the wrapping models put around them, and anything off-schema must
come back invalid rather than half-parsed, because a half-parsed verdict would
let a malformed answer steer a repair round.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "agent-loop" / "validate_verdict.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_verdict", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load()


def _finding(**overrides: object) -> dict[str, object]:
    finding: dict[str, object] = {
        "severity": "blocking",
        "file": "src/a.py",
        "line": 3,
        "title": "t",
        "detail": "d",
    }
    finding.update(overrides)
    return finding


def _answer(**overrides: object) -> str:
    answer: dict[str, object] = {"verdict": "changes", "summary": "s", "findings": [_finding()]}
    answer.update(overrides)
    return json.dumps(answer)


def test_a_clean_answer_parses() -> None:
    review = validator.parse(_answer(verdict="approve", findings=[]))
    assert review == {"verdict": "approve", "summary": "s", "findings": []}


def test_prose_and_code_fences_around_the_object_are_tolerated() -> None:
    # Copilot CLI is not schema-constrained and wraps its answer.
    wrapped = f"Here is my review:\n```json\n{_answer()}\n```\nThanks!"
    review = validator.parse(wrapped)
    assert review is not None
    assert review["findings"][0]["severity"] == "blocking"


@pytest.mark.parametrize("line", [None, True, "12", 1.5])
def test_a_missing_or_non_integer_line_becomes_zero(line: object) -> None:
    finding = _finding()
    if line is None:
        del finding["line"]
    else:
        finding["line"] = line
    review = validator.parse(_answer(findings=[finding]))
    assert review is not None
    assert review["findings"][0]["line"] == 0


def test_findings_are_capped_at_eight() -> None:
    review = validator.parse(_answer(findings=[_finding(file=f"f{i}") for i in range(12)]))
    assert review is not None
    assert len(review["findings"]) == validator.MAX_FINDINGS == 8


def test_unknown_finding_keys_are_dropped() -> None:
    review = validator.parse(_answer(findings=[_finding(extra="x")]))
    assert review is not None
    assert "extra" not in review["findings"][0]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "Error: You have exhausted your included AI credits.",
        "{not json}",
        "[1, 2, 3]",
        _answer(verdict="lgtm"),
        _answer(summary=None),
        _answer(findings="none"),
        _answer(findings=[_finding(severity="major")]),
        _answer(findings=[_finding(file=None)]),
        _answer(findings=[_finding(detail=7)]),
        _answer(findings=["not an object"]),
    ],
    ids=[
        "empty",
        "quota-error-text",
        "not-json",
        "not-an-object",
        "unknown-verdict",
        "summary-not-string",
        "findings-not-list",
        "unknown-severity",
        "file-missing",
        "detail-not-string",
        "finding-not-object",
    ],
)
def test_anything_off_schema_is_invalid(raw: str) -> None:
    assert validator.parse(raw) is None


def test_main_writes_valid_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = tmp_path / "raw.txt"
    raw.write_text(_answer(summary="two\nlines"), encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr("sys.argv", ["validate_verdict.py", str(raw)])
    validator.main()
    lines = out.read_text(encoding="utf-8").splitlines()
    # One line per output: a newline in model text must not break GITHUB_OUTPUT.
    assert lines[0] == "valid=true"
    assert len(lines) == 2
    assert json.loads(lines[1].removeprefix("review="))["summary"] == "two\nlines"


def test_main_reports_invalid_for_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr("sys.argv", ["validate_verdict.py", str(tmp_path / "absent")])
    validator.main()
    assert out.read_text(encoding="utf-8") == "valid=false\n"
