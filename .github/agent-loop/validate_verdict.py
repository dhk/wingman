"""Validate a reviewer's raw answer into the agent loop's verdict shape.

Usage: validate.py RAW_FILE
Writes `valid=true|false` and, when valid, `review=<compact JSON>` to
$GITHUB_OUTPUT. Never raises on bad model output: an unusable answer is
`valid=false`, which sends the loop to the next reviewer.

Codex and Claude are schema-constrained already; Copilot CLI is not, so its
answer may carry prose or code fences around the object. This takes the
outermost {...} span and checks every field the loop's deterministic steps
read. Findings are truncated to 8, matching the review prompt.
"""

import json
import os
import sys

MAX_FINDINGS = 8


def parse(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in ("approve", "changes"):
        return None
    if not isinstance(data.get("summary"), str) or not isinstance(data.get("findings"), list):
        return None
    findings = []
    for f in data["findings"][:MAX_FINDINGS]:
        if not isinstance(f, dict) or f.get("severity") not in ("blocking", "nit"):
            return None
        if not all(isinstance(f.get(k), str) for k in ("file", "title", "detail")):
            return None
        line = f.get("line", 0)
        findings.append(
            {
                "severity": f["severity"],
                "file": f["file"],
                "line": line if isinstance(line, int) and not isinstance(line, bool) else 0,
                "title": f["title"],
                "detail": f["detail"],
            }
        )
    return {"verdict": data["verdict"], "summary": data["summary"], "findings": findings}


def main() -> None:
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            review = parse(fh.read())
    except (IndexError, OSError):
        review = None
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        if review is None:
            out.write("valid=false\n")
        else:
            out.write("valid=true\n")
            out.write(f"review={json.dumps(review, separators=(',', ':'))}\n")


if __name__ == "__main__":
    main()
