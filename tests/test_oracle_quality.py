"""Re-derive spec 04's oracle scorecard from the committed corpus and assert it.

No Docker, no network, no new draw: `fixtures/verdict-corpus/` already holds each hidden
check's status on base, under gold and under half, which is the whole input.

**This is the caller.** `uv run pytest` collects it, and nothing on the `outcome run` path
calls `grade_corpus` by design -- the same arrangement as `tests/test_verdict_corpus.py`,
where the gate is the caller. Regenerate the fixture with:

    uv run python -m andromeda.outcome.grading.oracle_quality
"""

import ast
import functools
import inspect
import json
import re
import shutil
from pathlib import Path

import pytest

from andromeda.outcome.grading import oracle_quality as oq
from andromeda.outcome.grading.oracle_quality import (
    CORPUS,
    SCORECARD,
    classify,
    gold_cells,
    grade_corpus,
    to_tsv,
)

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_CELLS = 18
C580EBF0 = "qutebrowser-c580ebf0"

# AC4: spec 03's REPORT per-cell `fail` column for the gold cells -- the valid hidden checks
# a *correct* patch still cannot pass. Every one of them is a `wrong` check here, so the two
# specs' numbers are pinned against each other rather than each drifting alone.
SPEC_03_GOLD_FAIL = {
    (C580EBF0, "pinned"): 2, (C580EBF0, "draw-01"): 1, (C580EBF0, "draw-02"): 1,
    (C580EBF0, "draw-03"): 3, (C580EBF0, "draw-04"): 1, (C580EBF0, "draw-05"): 2,
}
EXPECTED_WRONG_TOTAL = 10

# AC4a's external pin. Spec 03's REPORT per-cell `valid` column for the gold cells -- the
# count `decide()` actually computed, transcribed from committed text. The corpus-derived
# check below re-spells decide()'s predicate, which `classify` also encodes, so on its own it
# could stay green through a change to what "valid" means. These 18 numbers cannot.
SPEC_03_GOLD_VALID = {
    ("ansible-f327e65d", "pinned"): 25, ("ansible-f327e65d", "draw-01"): 15,
    ("ansible-f327e65d", "draw-02"): 80, ("ansible-f327e65d", "draw-03"): 29,
    ("ansible-f327e65d", "draw-04"): 45, ("ansible-f327e65d", "draw-05"): 23,
    (C580EBF0, "pinned"): 19, (C580EBF0, "draw-01"): 16, (C580EBF0, "draw-02"): 15,
    (C580EBF0, "draw-03"): 19, (C580EBF0, "draw-04"): 18, (C580EBF0, "draw-05"): 19,
    ("qutebrowser-f91ace96", "pinned"): 10, ("qutebrowser-f91ace96", "draw-01"): 7,
    ("qutebrowser-f91ace96", "draw-02"): 12, ("qutebrowser-f91ace96", "draw-03"): 13,
    ("qutebrowser-f91ace96", "draw-04"): 10, ("qutebrowser-f91ace96", "draw-05"): 10,
}

REPORT = ROOT / "specs" / "04-oracle-quality" / "REPORT.md"
FENCE = ("<!-- scorecard:begin -->", "<!-- scorecard:end -->")
HEADLINE_FENCE = ("<!-- headline:begin -->", "<!-- headline:end -->")
HEADLINE_COLUMNS = ["task", "pinned", "best draw", "worst draw", "spread",
                    "draws beating pinned", "mean of the 5 draws"]

# AC6, read off the scorecard to 2dp.
AC6 = {
    "ansible-f327e65d": {"pinned": 0.68, "best": ("draw-02", 0.88), "worst": ("draw-01", 0.24)},
    C580EBF0: {"pinned": 0.25, "best": ("draw-02", 0.32), "worst": ("draw-04", 0.24)},
    "qutebrowser-f91ace96": {"pinned": 0.70, "best": ("draw-01", 0.86), "worst": ("draw-02", 0.58)},
}

SCORES = grade_corpus(CORPUS)


def by_key() -> dict[tuple[str, str], oq.OracleScore]:
    return {(s.task, s.oracle): s for s in SCORES}


@functools.lru_cache(maxsize=None)
def cell_checks(path: Path) -> list[dict]:
    """Cached: 36 cells are re-read by every parametrized case, and AC10 caps the file at 1s."""
    with path.open() as fh:
        return json.load(fh)["checks"]


def hidden_checks(path: Path) -> list[dict]:
    return [c for c in cell_checks(path) if c["kind"] == "hidden"]


# --- AC1: classify ---------------------------------------------------------------------

@pytest.mark.parametrize("before,gold,half,expected", [
    ("pass", "pass", "pass", "vacuous"),
    ("pass", "fail", "fail", "vacuous"),   # the `*` columns really are ignored
    ("fail", "fail", "fail", "wrong"),
    ("fail", "fail", "pass", "wrong"),
    ("fail", "error", "fail", "wrong"),    # "not pass", not "fail" exactly
    ("fail", "pass", "pass", "blunt"),
    ("fail", "pass", "fail", "good"),
    ("fail", "pass", "error", "good"),
])
def test_classify_matches_the_spec_table(before, gold, half, expected):
    assert classify(before, gold, half) == expected


def test_a_check_that_errors_on_base_is_vacuous():
    """decide() counts valid as `before == "fail"` exactly, so anything else tests nothing.

    Unexercised by the corpus today (see `test_no_hidden_check_errors_on_base`), which is why
    it is pinned here: the identity AC4 asserts holds by construction, not by luck.
    """
    assert classify("error", "pass", "fail") == "vacuous"
    assert classify("error", "fail", "fail") == "vacuous"


def test_classify_is_pure():
    """AC1: no I/O, no corpus path, no GradingData -- it takes three statuses and returns."""
    src = inspect.getsource(classify)
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    for forbidden in ("open(", "Path", "read_text", "json", "CORPUS", "GradingData", "load("):
        assert forbidden not in body, f"classify() mentions {forbidden!r}"
    assert list(inspect.signature(classify).parameters) == ["before", "gold_after", "half_after"]


def test_the_module_imports_nothing_that_needs_docker_or_the_answers():
    """AC10 (no Docker, no network) and AC1 (no GradingData), asserted at the import graph."""
    imports = set()
    for node in ast.walk(ast.parse(Path(oq.__file__).read_text())):
        if isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    first_party = {n for n in imports if n.startswith("andromeda")}
    assert first_party == {"andromeda.outcome.core.types"}, \
        f"oracle_quality imports more than core.types: {sorted(first_party)}"


# --- AC2/AC3: the corpus walk ----------------------------------------------------------

def test_eighteen_scores():
    assert len(SCORES) == EXPECTED_CELLS, f"{len(SCORES)} scores, not {EXPECTED_CELLS}"
    assert len(by_key()) == EXPECTED_CELLS, "duplicate (task, oracle) in the scorecard"
    assert [(s.task, s.oracle) for s in SCORES] == sorted((s.task, s.oracle) for s in SCORES)


def test_a_gold_cell_with_no_half_beside_it_raises(tmp_path):
    """AC2: skipping it silently would drop a row and still produce a plausible scorecard."""
    cell = gold_cells(CORPUS)[0]
    dest = tmp_path / cell.parent.parent.name / cell.parent.name
    dest.mkdir(parents=True)
    shutil.copy(cell, dest / "gold.json")

    with pytest.raises(FileNotFoundError, match="half.json"):
        grade_corpus(tmp_path)

    # and the same cell grades fine once half.json is beside it, so the raise above is the
    # missing file and not the copy being malformed
    shutil.copy(cell.with_name("half.json"), dest / "half.json")
    assert len(grade_corpus(tmp_path)) == 1


def test_zero_unpaired_hidden_names_across_all_eighteen_pairs():
    """AC3. grade_corpus raises on an unpaired name; this counts them, so 0 is asserted."""
    unpaired, pairs = {}, 0
    for gold in gold_cells(CORPUS):
        half = gold.with_name("half.json")
        assert half.is_file(), f"no half.json beside {gold.relative_to(ROOT)}"
        pairs += 1
        g = {c["name"] for c in hidden_checks(gold)}
        h = {c["name"] for c in hidden_checks(half)}
        if g ^ h:
            unpaired[str(gold.parent.relative_to(CORPUS))] = sorted(g ^ h)
    assert pairs == EXPECTED_CELLS, f"walked {pairs} pairs, not {EXPECTED_CELLS}"
    assert unpaired == {}, f"hidden checks present in one cell but not the other: {unpaired}"


def test_the_corpus_is_whole():
    """Every assertion above iterates the corpus, so a truncated one passes vacuously."""
    cells = gold_cells(CORPUS)
    assert len(cells) == EXPECTED_CELLS
    assert {c.parent.parent.name for c in cells} == {p.name for p in CORPUS.iterdir() if p.is_dir()}
    for cell in cells:
        assert hidden_checks(cell), f"{cell.relative_to(ROOT)} recorded no hidden checks"
        assert hidden_checks(cell.with_name("half.json")), \
            f"{cell.with_name('half.json').relative_to(ROOT)} recorded no hidden checks"


def test_no_hidden_check_errors_on_base():
    """Decision 1's `before == "error"` branch must stay unexercised, or visibly stop being so.

    378 `existing` checks error on base; 0 hidden ones do. If that ever changes, checks start
    landing in `vacuous` for a reason the scorecard does not name, and this fails first.
    """
    erroring = [
        f"{cell.parent.relative_to(CORPUS)}: {c['name']}"
        for cell in gold_cells(CORPUS) for c in hidden_checks(cell) if c["before"] == "error"
    ]
    assert erroring == [], f"hidden checks now error on base: {erroring}"
    assert any(c["before"] == "error"
               for cell in gold_cells(CORPUS)
               for c in cell_checks(cell)), \
        "no check of any kind errors on base -- the assertion above is vacuous"


# --- AC4: cross-check against committed data, per oracle -------------------------------

@pytest.mark.parametrize("score", SCORES, ids=lambda s: f"{s.task}-{s.oracle}")
def test_n_hidden_minus_vacuous_is_the_valid_count_decide_computes(score):
    """AC4a, per oracle. Same predicate as verdict.decide(): `kind == hidden and before == fail`."""
    cell = next(c for c in gold_cells(CORPUS)
                if oq.short_task(c.parent.parent.name) == score.task
                and c.parent.name == score.oracle)
    valid = [c for c in hidden_checks(cell) if c["before"] == "fail"]
    assert score.n_hidden - score.vacuous == len(valid), (
        f"{score.task} {score.oracle}: n_hidden - vacuous = "
        f"{score.n_hidden - score.vacuous}, but decide() sees {len(valid)} valid hidden checks")
    assert score.wrong + score.blunt + score.good == len(valid)
    assert score.vacuous + score.wrong + score.blunt + score.good == score.n_hidden

    # and against spec 03's committed number for the same cell, which no code here produces
    assert score.n_hidden - score.vacuous == SPEC_03_GOLD_VALID[(score.task, score.oracle)], (
        f"{score.task} {score.oracle}: n_hidden - vacuous = {score.n_hidden - score.vacuous}, "
        f"but 03-verdict-rules/REPORT.md records "
        f"{SPEC_03_GOLD_VALID[(score.task, score.oracle)]} valid hidden checks")


def test_the_spec_03_tables_name_every_cell():
    """Both cross-checks index by (task, oracle), so a renamed cell would skip silently."""
    keys = set(by_key())
    assert set(SPEC_03_GOLD_VALID) == keys, \
        f"SPEC_03_GOLD_VALID does not cover the corpus: {set(SPEC_03_GOLD_VALID) ^ keys}"
    assert set(SPEC_03_GOLD_FAIL) <= keys, \
        f"SPEC_03_GOLD_FAIL names cells that do not exist: {set(SPEC_03_GOLD_FAIL) - keys}"


def test_wrong_equals_spec_03s_gold_fail_column():
    """AC4b: 10 in total, all on c580ebf0, (2,1,1,3,1,2) for pinned then draw-01..05."""
    scores = by_key()
    for key, expected in SPEC_03_GOLD_FAIL.items():
        assert scores[key].wrong == expected, (
            f"{key[0]} {key[1]}: wrong={scores[key].wrong}, but spec 03's REPORT records "
            f"{expected} valid hidden checks failing under gold")
    elsewhere = {k: s.wrong for k, s in scores.items() if k[0] != C580EBF0 and s.wrong}
    assert elsewhere == {}, f"`wrong` checks outside {C580EBF0}: {elsewhere}"
    assert sum(s.wrong for s in SCORES) == EXPECTED_WRONG_TOTAL


# --- AC5: the committed scorecard -------------------------------------------------------

def test_the_committed_scorecard_matches_the_corpus_row_for_row():
    """AC5. A hand-edited scorecard or a drifting classifier fails here, naming the row."""
    assert SCORECARD.is_file(), (
        f"missing {SCORECARD.relative_to(ROOT)} -- regenerate with "
        "`uv run python -m andromeda.outcome.grading.oracle_quality`")
    committed = SCORECARD.read_text().splitlines()
    derived = to_tsv(SCORES).splitlines()

    assert committed[0] == derived[0] == "\t".join(oq.COLUMNS)
    assert len(committed) - 1 == EXPECTED_CELLS, \
        f"{len(committed) - 1} rows in {SCORECARD.name}, not {EXPECTED_CELLS}"
    for n, (want, got) in enumerate(zip(derived[1:], committed[1:]), start=2):
        assert got == want, (
            f"{SCORECARD.relative_to(ROOT)} line {n} disagrees with the corpus:\n"
            f"  committed: {got}\n  derived:   {want}")
    assert committed == derived


# --- AC6/AC7: the findings ---------------------------------------------------------------

@pytest.mark.parametrize("task", sorted(AC6))
def test_the_scores_ac6_names(task):
    cells = [s for s in SCORES if s.task == task]
    assert len(cells) == 6, f"{task}: {len(cells)} oracles, not 6"
    scores = {s.oracle: round(s.score, 2) for s in cells}
    draws = {o: v for o, v in scores.items() if o != "pinned"}

    assert scores["pinned"] == AC6[task]["pinned"]
    best_oracle, best_score = AC6[task]["best"]
    worst_oracle, worst_score = AC6[task]["worst"]
    assert max(draws, key=lambda o: draws[o]) == best_oracle
    assert draws[best_oracle] == best_score
    assert min(draws, key=lambda o: draws[o]) == worst_oracle
    assert draws[worst_oracle] == worst_score


@pytest.mark.parametrize("task", sorted(AC6))
def test_the_pinned_oracle_scores_below_the_best_draw(task):
    """AC7, the finding that motivates spec 12: spec 01's single draw is never the best one.

    Only "below the best draw" is asserted, because only that holds on all three tasks. The
    stronger reading -- that the pin is at least an above-average draw -- is false on
    `qutebrowser-c580ebf0`, where pinned 0.25 sits under the draws' mean and median of 0.28
    and four of five draws beat it. See REPORT.md's headline table.
    """
    cells = [s for s in SCORES if s.task == task]
    pinned = next(s for s in cells if s.oracle == "pinned")
    best = max((s for s in cells if s.oracle != "pinned"), key=lambda s: s.score)
    assert pinned.score < best.score, (
        f"{task}: pinned scores {pinned.score:.2f}, best draw {best.oracle} scores "
        f"{best.score:.2f} -- pinned is no longer beaten, so AC7's finding has changed")


# --- AC8: REPORT.md round trip -----------------------------------------------------------
#
# Same arrangement as spec 02's `test_report_table_is_derivable_from_the_committed_tsv`.
# "Re-derivable from the scorecard" was verified by hand when the report was written; this
# is what keeps it true, so an edited table or a regenerated scorecard cannot drift apart.

def parse_report_table(fence: tuple[str, str] = FENCE) -> list[list[str]]:
    assert REPORT.is_file(), f"missing {REPORT.relative_to(ROOT)}"
    text = REPORT.read_text()
    start, end = fence
    assert start in text and end in text, f"{REPORT.relative_to(ROOT)}: missing {start}/{end}"
    body = text.split(start, 1)[1].split(end, 1)[0]
    lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    assert lines, "no pipe table between the fences"
    rows = [[c.strip().replace("**", "") for c in ln.strip("|").split("|")] for ln in lines]
    return [r for r in rows if not all(re.fullmatch(r":?-{3,}:?", c) for c in r)]


def test_the_report_table_is_derivable_from_the_committed_scorecard():
    """AC8. An edited REPORT.md or a stale scorecard fails here, naming the row.

    Matched by (task, oracle), not by position: the report lists each task's pinned oracle
    first because that is how it reads, while the scorecard sorts `(task, oracle)` as the
    spec fixes it. The cells must agree; the row order is presentation.
    """
    parsed = parse_report_table()
    assert parsed[0] == oq.COLUMNS, f"REPORT.md header {parsed[0]} != {oq.COLUMNS}"
    assert len(parsed) - 1 == EXPECTED_CELLS, \
        f"{len(parsed) - 1} rows between the fences, not {EXPECTED_CELLS}"

    derived = {tuple(r[:2]): r for r in
               (line.split("\t") for line in to_tsv(SCORES).splitlines()[1:])}
    reported = {tuple(r[:2]): r for r in parsed[1:]}
    assert set(reported) == set(derived), (
        f"{REPORT.relative_to(ROOT)} names different cells than the scorecard: "
        f"{sorted(set(reported) ^ set(derived))}")
    for key, want in sorted(derived.items()):
        assert reported[key] == want, (
            f"{REPORT.relative_to(ROOT)} row {key[0]} {key[1]} disagrees with the corpus:\n"
            f"  report:  {reported[key]}\n  derived: {want}")


def test_the_report_parser_is_not_vacuous():
    """The falsifier for the test above: it must reject a table it should reject.

    `parse_report_table` strips `**`, so a bolded cell is equal to its plain form on purpose;
    a changed *number* must not be.
    """
    parsed = parse_report_table()
    assert len(parsed) == EXPECTED_CELLS + 1 and len(parsed[0]) == len(oq.COLUMNS)
    derived = {tuple(r[:2]): r for r in
               (line.split("\t") for line in to_tsv(SCORES).splitlines()[1:])}
    for row in parsed[1:]:
        tampered = row[:]
        tampered[-1] = "9.99"
        assert tampered != derived[tuple(row[:2])], \
            f"the parser cannot tell a wrong score from a right one: {row}"


def test_the_headline_table_is_derivable_from_the_scorecard():
    """AC8, for the derived columns the per-cell table does not contain.

    `draws beating pinned` and `mean of the 5 draws` are computed on the **unrounded**
    `good / n_hidden`, which is not the same count as the scorecard's 2dp column: on
    `ansible-f327e65d`, draw-05 (23/34 = 0.6765) beats pinned (25/37 = 0.6757) while both
    render as 0.68. Rounding first would read 2 of 5 and lose it, so the basis is asserted
    here rather than left to whoever next edits the prose.
    """
    parsed = parse_report_table(HEADLINE_FENCE)
    assert parsed[0] == HEADLINE_COLUMNS, f"headline header {parsed[0]} != {HEADLINE_COLUMNS}"
    assert len(parsed) - 1 == 3, f"{len(parsed) - 1} tasks in the headline table, not 3"
    # the row set, not just the count: three rows of the right shape could otherwise be one
    # task repeated, and every per-row assertion below would still pass
    assert {r[0] for r in parsed[1:]} == {s.task for s in SCORES}, (
        f"the headline table names {sorted({r[0] for r in parsed[1:]})}, "
        f"not the corpus's {sorted({s.task for s in SCORES})}")

    for row in parsed[1:]:
        task = row[0]
        cells = [s for s in SCORES if s.task == task]
        assert len(cells) == 6, f"{task}: {len(cells)} oracles, not 6"
        pinned = next(s for s in cells if s.oracle == "pinned")
        draws = [s for s in cells if s.oracle != "pinned"]
        best, worst = max(draws, key=lambda s: s.score), min(draws, key=lambda s: s.score)

        want = [
            task,
            f"{pinned.score:.2f}",
            f"{best.score:.2f} ({best.oracle})",
            f"{worst.score:.2f} ({worst.oracle})",
            f"{best.score - worst.score:.2f}",
            f"{sum(1 for d in draws if d.score > pinned.score)} of 5",
            f"{sum(d.score for d in draws) / len(draws):.2f}",
        ]
        assert row == want, (
            f"{REPORT.relative_to(ROOT)} headline row {task} disagrees with the corpus:\n"
            f"  report:  {row}\n  derived: {want}")
