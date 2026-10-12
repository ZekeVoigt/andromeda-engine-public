"""Spec 18's measurement of the harness's own inputs: which gold test patches edit a base test
module (L37), and which selected test files the harness's setup command restores.

    uv run python -m andromeda.outcome.grading.restore

scripts/in_process_scan.py runs this as a subprocess and prints its output: a script may neither
read grading data nor import this package (tests/test_boundaries.py), and this package is the
one place that may.
"""

import ast
import shlex
import sys

from andromeda.outcome.adapters.coding.infra import Unreadable, split
from andromeda.outcome.adapters.coding.task import _dataset


def _python_rows() -> list[dict]:
    return [r for r in _dataset() if r["repo_language"] == "python"]


def gold_tests() -> dict[str, str]:
    """instance_id -> the gold test patch, for every Python row."""
    return {r["instance_id"]: r["test_patch"] for r in _python_rows()}


def restored_by(setup: str) -> set[str] | None:
    """The files `git checkout <sha> -- <files>` on the setup command's last line restores."""
    words = shlex.split(setup.strip().split("\n")[-1])
    if words[:2] != ["git", "checkout"] or "--" not in words:
        return None
    return set(words[words.index("--") + 1:])


def unrestored() -> tuple[int, list[tuple[str, list[str]]]]:
    """(Python rows, [(instance_id, selected files its setup does not restore)]): by file, the
    file part of a `path::node` entry, since `git checkout` restores files."""
    rows, short = _python_rows(), []
    for r in rows:
        selected = {e.split("::", 1)[0] for e in ast.literal_eval(r["selected_test_files_to_run"])}
        missing = sorted(selected - (restored_by(r["before_repo_set_cmd"]) or set()))
        if missing:
            short.append((r["instance_id"], missing))
    return len(rows), short


def main() -> int:
    tests = gold_tests()
    edits = removes = unreadable = 0
    for patch in tests.values():
        try:
            base = split(patch).tests
        except Unreadable:
            unreadable += 1
            continue
        edits += bool(base)
        removes += any(f"rename from {t}\n" in patch or f"--- a/{t}\n+++ /dev/null" in patch for t in base)
    print(f"| gold test patch, Python rows | {len(tests)} | {edits} | {removes} | {unreadable} |")
    rows, short = unrestored()
    print(f"\n## the harness's restore: {rows - len(short)}/{rows} Python rows restore every selected file\n")
    for iid, missing in short:
        print(f"  {iid}: not restored: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
