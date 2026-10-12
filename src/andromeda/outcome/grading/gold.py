"""Writes the official fix (source files only) for one task, as the control patch of the bad-patch check."""

import re
import sys
from pathlib import Path

from andromeda.outcome.adapters.coding.adapter import is_test_file, touched_files
from andromeda.outcome.adapters.coding.task import GradingData, load


def source_only(diff: str) -> str:
    """Drop `diff --git` blocks whose file is a test file."""
    blocks = re.split(r"^(?=diff --git )", diff, flags=re.M)
    return "".join(b for b in blocks if not any(is_test_file(f) for f in touched_files(b)))


def gold_patch(instance_id: str) -> str:
    _, grading = load(instance_id)
    grading: GradingData
    return source_only(grading.patch)


if __name__ == "__main__":
    instance_id, out_file = sys.argv[1], Path(sys.argv[2])
    patch = gold_patch(instance_id)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(patch)
    print("\n".join(touched_files(patch)))
