#!/usr/bin/env python3
"""Regenerate the coverage snapshot in `tests/COVERAGE.md`.

Runs the full pytest suite under coverage and rewrites the block between
the ``<!-- coverage-snapshot:start -->`` / ``<!-- coverage-snapshot:end -->``
markers with a fresh Markdown table. Exits non-zero if any test fails so a
broken suite can't silently land a stale-looking snapshot.

Usage:
    uv run python scripts/update_coverage_report.py
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "tests" / "COVERAGE.md"
START_MARKER = "<!-- coverage-snapshot:start -->"
END_MARKER = "<!-- coverage-snapshot:end -->"


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


def main() -> int:
    if not REPORT_PATH.exists():
        sys.stderr.write(f"missing {REPORT_PATH}\n")
        return 1

    print("• running pytest with coverage…", flush=True)
    # --cov-report= disables the terminal report; we'll generate a markdown
    # one from the .coverage data file below.
    test_run = _run(
        ["uv", "run", "pytest", "--cov", "--cov-report=", "-q"]
    )
    if test_run.returncode != 0:
        sys.stderr.write(test_run.stdout)
        sys.stderr.write(test_run.stderr)
        return test_run.returncode

    summary_match = re.search(r"(\d+ passed[^\n]*)", test_run.stdout)
    test_summary = summary_match.group(1).strip() if summary_match else "unknown"
    print(f"  → {test_summary}", flush=True)

    print("• generating markdown coverage table…", flush=True)
    md_run = _run(["uv", "run", "coverage", "report", "--format=markdown"])
    if md_run.returncode != 0:
        sys.stderr.write(md_run.stdout)
        sys.stderr.write(md_run.stderr)
        return md_run.returncode
    table = md_run.stdout.rstrip()

    total_match = re.search(
        r"\|\s*\*\*TOTAL\*\*\s*\|.*?\*\*\s*([\d.]+%)\s*\*\*",
        table,
    )
    total_pct = total_match.group(1) if total_match else "?"

    today = dt.date.today().isoformat()
    new_block = (
        f"{START_MARKER}\n"
        f"_Regenerated on **{today}** — tests: {test_summary} — "
        f"total coverage: **{total_pct}**._\n\n"
        f"{table}\n"
        f"{END_MARKER}"
    )

    content = REPORT_PATH.read_text()
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL
    )
    if not pattern.search(content):
        sys.stderr.write(
            f"snapshot markers not found in {REPORT_PATH}\n"
            f"expected {START_MARKER!r} and {END_MARKER!r}\n"
        )
        return 1

    REPORT_PATH.write_text(pattern.sub(new_block, content))
    print(f"✓ updated {REPORT_PATH.relative_to(REPO_ROOT)} (total: {total_pct})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
