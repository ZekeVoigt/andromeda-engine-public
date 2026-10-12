"""Grade how well each pinned and drawn oracle's hidden tests discriminate.

Spec 04. Every hidden check in `fixtures/verdict-corpus/` carries three recorded
observations -- its status on base, under the gold patch, and under the half patch -- which
is enough to classify it without Docker, a new draw, or a re-run.

Under `grading/` because it reads ground truth (the gold patch is the answer the engine is
meant to find on its own). That placement is also what stops `adapters/` and `scripts/`
importing it: `tests/test_boundaries.py::test_ac7_adapters_and_scripts_import_nothing_from_grading`.

The grade never reaches `decide()`. Making the verdict a function of the gold patch would
defeat the point of writing hidden tests instead of reading the answer.

Regenerate the committed scorecard with:

    uv run python -m andromeda.outcome.grading.oracle_quality
"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from andromeda.outcome.core.types import Check, Status

ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "fixtures" / "verdict-corpus"
SCORECARD = ROOT / "fixtures" / "oracle-quality" / "scorecard.tsv"

COLUMNS = ["task", "oracle", "n_hidden", "vacuous", "wrong", "blunt", "good", "score"]

Klass = Literal["vacuous", "wrong", "blunt", "good"]


def classify(before: Status, gold_after: Status, half_after: Status) -> Klass:
    """One hidden check's three observations -> its class. Pure: no I/O, no corpus, no answers.

        before   gold    half    class
        ------   ----    ----    --------
        pass     *       *       vacuous    passed before the change: tests nothing
        fail     fail    *       wrong      a correct patch cannot pass it
        fail     pass    pass    blunt      valid, but a known-broken patch passes too
        fail     pass    fail    good       discriminates
    """
    # `before == "error"` lands here too, and is deliberately vacuous rather than a class of
    # its own: decide() counts a check as valid when `before == "fail"` exactly, so writing
    # the predicate this way makes AC4's identity (n_hidden - vacuous == valid) true by
    # construction instead of by luck. No hidden check in the corpus errors on base today --
    # 378 `existing` checks do, 0 hidden -- so this branch is unexercised by real data, and
    # `test_a_check_that_errors_on_base_is_vacuous` plus the corpus guard keep that visible.
    if before != "fail":
        return "vacuous"
    if gold_after != "pass":
        return "wrong"
    return "blunt" if half_after == "pass" else "good"


class OracleScore(BaseModel):
    task: str
    oracle: str
    n_hidden: int
    vacuous: int
    wrong: int
    blunt: int
    good: int
    score: float


def short_task(instance_id: str) -> str:
    """instance_<org>__<repo>-<sha>-v... -> <repo>-<sha[:8]>

    Copied from `scripts/oracle_variance.py`, not imported: `grading/` must not depend on
    `scripts/`. Matches `fixtures/variance/results.tsv` and spec 03's REPORT.
    """
    m = re.search(r"__(.+?)-([0-9a-f]{40})", instance_id)
    return f"{m.group(1)}-{m.group(2)[:8]}" if m else instance_id


def _label(path: Path) -> str:
    """A path for an error message, ROOT-relative where it can be. Total: `grade_corpus` is
    called on `tmp_path` by the gate, and a message must not raise on the way out."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def hidden_by_name(cell: Path) -> dict[str, Check]:
    with cell.open() as fh:
        checks = [Check.model_validate(c) for c in json.load(fh)["checks"]]
    return {c.name: c for c in checks if c.kind == "hidden"}


def grade_cell(gold_path: Path) -> OracleScore:
    """Grade one (task, oracle) pair from its gold cell and the half cell beside it."""
    half_path = gold_path.with_name("half.json")
    if not half_path.is_file():
        raise FileNotFoundError(
            f"no half.json beside {_label(gold_path)}: a cell cannot be graded from "
            "the gold patch alone, and skipping it would drop a row from the scorecard")

    gold, half = hidden_by_name(gold_path), hidden_by_name(half_path)

    unpaired = sorted(set(gold) ^ set(half))
    if unpaired:
        raise ValueError(
            f"{_label(gold_path.parent)}: {len(unpaired)} hidden check(s) in one "
            f"cell but not the other, so a count would silently drop them: {unpaired}")

    # `before` is read from the gold cell; the half cell observed the same base commit and
    # must agree. Free to assert, and it catches a corrupted half cell that would otherwise
    # shift the counts with nothing failing.
    disagree = sorted(n for n in gold if gold[n].before != half[n].before)
    if disagree:
        raise ValueError(
            f"{_label(gold_path.parent)}: gold and half disagree on the base "
            f"status of {len(disagree)} hidden check(s): "
            + ", ".join(f"{n} ({gold[n].before} vs {half[n].before})" for n in disagree))

    counts = {"vacuous": 0, "wrong": 0, "blunt": 0, "good": 0}
    for name, check in gold.items():
        counts[classify(check.before, check.after, half[name].after)] += 1

    with gold_path.open() as fh:
        cell = json.load(fh)
    n_hidden = len(gold)
    return OracleScore(
        task=short_task(cell["task"]),
        oracle=cell["oracle"],
        n_hidden=n_hidden,
        score=counts["good"] / n_hidden if n_hidden else 0.0,
        **counts,
    )


def gold_cells(corpus: Path) -> list[Path]:
    """Every oracle cell's gold.json.

    `<task>/sources/gold.json` is excluded: it holds the existing-test source listing the
    diff's file selection reads, not a cell's checks. Same exclusion as
    `tests/test_verdict_corpus.py::cell_files`.
    """
    return sorted(p for p in corpus.glob("*/*/gold.json") if p.parent.name != "sources")


def grade_corpus(corpus: Path) -> list[OracleScore]:
    """Every `<task>/<oracle>/gold.json` under `corpus`, graded, sorted by (task, oracle)."""
    return sorted((grade_cell(p) for p in gold_cells(corpus)),
                  key=lambda s: (s.task, s.oracle))


def to_tsv(scores: list[OracleScore]) -> str:
    rows = ["\t".join(COLUMNS)]
    for s in scores:
        rows.append("\t".join([
            s.task, s.oracle, str(s.n_hidden), str(s.vacuous), str(s.wrong),
            str(s.blunt), str(s.good), f"{s.score:.2f}"]))
    return "\n".join(rows) + "\n"


def report(scores: list[OracleScore]) -> str:
    """The per-cell table and the pinned-vs-best-draw summary REPORT.md is derived from."""
    width = {c: max(len(c), *(len(_cell(s, c)) for s in scores)) for c in COLUMNS}
    lines = ["  ".join(c.ljust(width[c]) for c in COLUMNS)]
    for s in scores:
        lines.append("  ".join(_cell(s, c).ljust(width[c]) for c in COLUMNS))

    lines += ["", "task                  pinned  best draw        worst draw"]
    for task in sorted({s.task for s in scores}):
        cells = [s for s in scores if s.task == task]
        pinned = next(s for s in cells if s.oracle == "pinned")
        draws = [s for s in cells if s.oracle != "pinned"]
        best, worst = max(draws, key=lambda s: s.score), min(draws, key=lambda s: s.score)
        lines.append(
            f"{task:<21} {pinned.score:>5.2f}   "
            f"{best.score:.2f} ({best.oracle})  {worst.score:.2f} ({worst.oracle})")
    return "\n".join(lines)


def _cell(score: OracleScore, column: str) -> str:
    value = getattr(score, column)
    return f"{value:.2f}" if column == "score" else str(value)


if __name__ == "__main__":
    scores = grade_corpus(CORPUS)
    SCORECARD.parent.mkdir(parents=True, exist_ok=True)
    SCORECARD.write_text(to_tsv(scores))
    print(f"wrote {len(scores)} rows to {SCORECARD.relative_to(ROOT)}\n")
    print(report(scores))
