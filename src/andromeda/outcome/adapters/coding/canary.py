"""Spec 18: canaries -- planted checks that must fail, beside the checks both graders run.

A canary is a test module written for one grading, in the directory of the checks it guards,
so it runs under the same conftest chain, rootdir and inifile. Its one test imports nothing and
fails by assertion: a canary reported passing proves that the run's report is false (L36).
Names are the hidden files' own prefix plus a fresh random draw, so a generic tamper keyed on
the oracle's naming reaches the canary too. Pure apart from `secrets`; both graders call it
through the module, so a test can replace `plant`.
"""

import secrets
from pathlib import PurePosixPath

CANARIES_PER_DIR = 1

SOURCE = "def test_{token}():\n    assert 1 == 2\n"


def plant(check_files: list[str]) -> dict[str, str]:
    """Repo-relative canary path -> source: CANARIES_PER_DIR per distinct directory of
    `check_files`, never equal to one of them."""
    taken, planted = set(check_files), {}
    for d in sorted({str(PurePosixPath(p).parent) for p in check_files}):
        for _ in range(CANARIES_PER_DIR):
            while True:
                token = secrets.token_hex(8)
                path = str(PurePosixPath(d) / f"test_outcome_{token}.py")
                if path not in taken and path not in planted:
                    break
            planted[path] = SOURCE.format(token=secrets.token_hex(8))
    return planted


def as_diff(planted: dict[str, str]) -> str:
    """`new file mode 100644` blocks for `planted`, in path order, that `git apply` accepts."""
    out = []
    for path, source in sorted(planted.items()):
        lines = source.splitlines()
        body = "".join(f"+{line}\n" for line in lines)
        out.append(f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
                   f"@@ -0,0 +1,{len(lines)} @@\n{body}")
    return "".join(out)
