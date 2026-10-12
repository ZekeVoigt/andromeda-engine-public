# Spec 20 report — observed regressions

L40 named six channels through which a result the diff's own code reports still decides a
verdict. Two are now closed by observation. The other four are recorded as unobservable, with
the argument, which goes to OPEN-ITEMS' **Settled** at landing.

**Measured for real:**
- A non-test-module file under a nested `tests/` directory is ignored as `test-support`. Over
  266 Python gold patches it costs **0**, and 0 of the 35 distinct recorded agent diffs touch
  one. It newly reaches 10 openlibrary `test_patch` rows, all test data the harness applies
  itself.
- The sealed slugify bundle grades gold **PASS**, bad **FAIL** and keep-broken **UNKNOWN /
  `regressed`** in the pinned ansible image. Neither of gold's two boxes holds any form of the
  sealed value.

## L40's channels and how each is closed

| # | channel | disposition | evidence |
|---|---|---|---|
| 1 | an edit to a helper under a nested `tests/` directory (`openlibrary/tests/helper.py`) | **built** (ACs 1–3) | `infra.NESTED_TEST_ROOTS`; the helper's edit, creation, deletion and rename are ignored, so the existing check runs the base helper |
| 2 | a `checks` bundle's `change` check, judged by exit status | **built** (ACs 4–6, 8) | `CheckSpec.expected_stdout`, held on the host and judged from the stdout `docker exec` hands back; `Evidence.sealed` says which case applies |
| 3 | a selective tamper that forces a pass only for the existing checks its change breaks | **recorded** | a regression check's expected value is base behaviour, and every diff holds base code, so withholding it seals nothing (the argument below) |
| 4 | a tamper that silences or steers around a canary | **recorded** | subsumed by 3: 18's canary is a generic detector by design, a silenced canary is absent and so `fail`, and a selective tamper never needs to touch one |
| 5 | sabotage of a hunk run's observations, so that spec 13 forgives a regression | **recorded** | subsumed by 3: a failure can always be written from inside the box, and code that can sabotage a hunk run can equally force-pass the regressed check in the main run |
| 6 | a `checks` bundle's `keep` check | **recorded** | it is 3 for an operator's check: `seal()` refuses an `expected_stdout` on a `keep` check for that reason |

### The argument (channels 3–6)

- **A regression check's expected value is base behaviour.** An existing check, or a `keep`
  check, passes on base and must still pass. Whatever value it compares, the value the host
  would expect is what base code produces for the check's inputs.
- **Every diff has base behaviour.** The code a diff carries can include a copy of any base
  function, and the grading box holds base in `/app/.git`. So a tamper can produce the base
  value for any observed input without the real code path keeping it. It cannot do that for
  19's answer, which a diff holds only if it solves the task.
- **So withholding gains nothing.** Lifting an existing check's literals to the host, or
  recording base's values in the before box, only raises the bar from "rewrite the report" to
  "carry base code", which every diff clears for free.
- **The check's truth is evaluated where the diff runs.** An existing check's asserts run in
  the interpreter that imported the diff's code, as root in the box. Moving them out would mean
  driving the code under test from a second process with values serialised across. The repos'
  tests monkeypatch module globals, build Qt objects, use `isinstance` and share fixtures, so
  they cannot run that way unchanged, and rewriting them would make the check the engine's.

Channel 1's residue is channel 3: a helper outside any `tests/` directory (openlibrary's
`openlibrary/mocks/`), or any source module a check imports. The path rule stops the plausible,
non-adversarial case: an agent that "fixes" a failing test by editing its helper.

## AC3: the scan

`uv run python scripts/gate_integrity_scan.py --root ~/Desktop/andromeda`, on the build:

```
# ScaleAI/SWE-bench_Pro@7ab5114: 731 rows, 266 of them Python

## gold patch (source side), Python rows: 266 diffs
  instance_ansible__ansible-b2a289dcbb702003377221e25f62c8a3608f0e89-v173091e2e36d38c978002990795f66cfc0af30ad: setup.cfg (config)
  instance_internetarchive__openlibrary-7c8dc180281491ccaa1b4b43518506323750d1e4-v298a7a812ceed28c4c18355a091f1b268fe56d86: pyproject.toml (config)
  instance_internetarchive__openlibrary-c506c1b0b678892af5cb22c1c1dbc35d96787a0a-v0f5aece3601a5b4419f7ccec1dbda2071be28ee4: pyproject.toml (config)
  instance_qutebrowser__qutebrowser-233cb1cc48635130e5602549856a6fa4ab4c087f-v35616345bb8052ea303186706cec663146f0f184: pytest.ini (config)
  -> 4 touch test infrastructure; per kind: {'config': 4}

# recorded agent diffs: 1042 agent.diff files, 35 distinct non-empty
  tasks: 3 real (ansible__ansible-f327e65d11bb90, qutebrowser__qutebrowser-c580eb, qutebrowser__qutebrowser-f91ace), 6 from scripted draws
  edit an existing test module (L37, not this rule): 7

## recorded agent diffs: 35 diffs
  -> 0 touch test infrastructure; per kind: {}

## verdict fixtures: 17 diffs
  -> 0 touch test infrastructure; per kind: {}

## dataset test_patch, paths only NESTED_TEST_ROOTS reaches (spec 20): 10 rows
  instance_internetarchive__openlibrary-08ac40d050a64e1d2646ece4959af0c42bf6b7b5-v0f5aece3601a5b4419f7ccec1dbda2071be28ee4: openlibrary/catalog/marc/tests/test_data/bin_expect/ithaca_college_75002321.json, openlibrary/catalog/marc/tests/test_data/bin_expect/lesnoirsetlesrou0000garl_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/memoirsofjosephf00fouc_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/warofrebellionco1473unit_meta.json, openlibrary/catalog/marc/tests/test_data/xml_expect/00schlgoog.json, openlibrary/catalog/marc/tests/test_data/xml_expect/warofrebellionco1473unit.json
  instance_internetarchive__openlibrary-0dc5b20fa186f9714f8a838178597e69f549d026-v2d9a6c849c60ed19fd0858ce9e40b7cc8e097e59: openlibrary/catalog/marc/tests/test_data/bin_expect/880_Nihon_no_chasho.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_arabic_french_many_linkages.json
  instance_internetarchive__openlibrary-111347e9583372e8ef91c82e0612ea437ae3a9c9-v2d9a6c849c60ed19fd0858ce9e40b7cc8e097e59: openlibrary/catalog/marc/tests/test_data/bin_expect/880_arabic_french_many_linkages.json, openlibrary/catalog/marc/tests/test_data/xml_expect/nybc200247.json, openlibrary/catalog/marc/tests/test_data/xml_input/nybc200247_marc.xml
  instance_internetarchive__openlibrary-11838fad1028672eb975c79d8984f03348500173-v0f5aece3601a5b4419f7ccec1dbda2071be28ee4: openlibrary/catalog/marc/tests/test_data/bin_expect/13dipolarcycload00burk_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/710_org_name_in_direct_order.json, openlibrary/catalog/marc/tests/test_data/bin_expect/830_series.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_Nihon_no_chasho.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_alternate_script.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_arabic_french_many_linkages.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_publisher_unlinked.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_table_of_contents.json, openlibrary/catalog/marc/tests/test_data/bin_expect/bijouorannualofl1828cole_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/bpl_0486266893.json, openlibrary/catalog/marc/tests/test_data/bin_expect/collingswood_520aa.json, openlibrary/catalog/marc/tests/test_data/bin_expect/collingswood_bad_008.json, openlibrary/catalog/marc/tests/test_data/bin_expect/cu31924091184469_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/diebrokeradical400poll_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/engineercorpsofh00sher_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/flatlandromanceo00abbouoft_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/histoirereligieu05cr_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/ithaca_college_75002321.json, openlibrary/catalog/marc/tests/test_data/bin_expect/ithaca_two_856u.json, openlibrary/catalog/marc/tests/test_data/bin_expect/lc_0444897283.json, openlibrary/catalog/marc/tests/test_data/bin_expect/lc_1416500308.json, openlibrary/catalog/marc/tests/test_data/bin_expect/lesnoirsetlesrou0000garl_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/memoirsofjosephf00fouc_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/merchantsfromcat00ben_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/ocm00400866.json, openlibrary/catalog/marc/tests/test_data/bin_expect/onquietcomedyint00brid_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/secretcodeofsucc00stjo_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/talis_740.json, openlibrary/catalog/marc/tests/test_data/bin_expect/talis_856.json, openlibrary/catalog/marc/tests/test_data/bin_expect/talis_empty_245.json, openlibrary/catalog/marc/tests/test_data/bin_expect/talis_multi_work_tiles.json, openlibrary/catalog/marc/tests/test_data/bin_expect/talis_no_title.json, openlibrary/catalog/marc/tests/test_data/bin_expect/talis_two_authors.json, openlibrary/catalog/marc/tests/test_data/bin_expect/test-publish-sn-sl-nd.json, openlibrary/catalog/marc/tests/test_data/bin_expect/test-publish-sn-sl.json, openlibrary/catalog/marc/tests/test_data/bin_expect/uoft_4351105_1626.json, openlibrary/catalog/marc/tests/test_data/bin_expect/upei_broken_008.json, openlibrary/catalog/marc/tests/test_data/bin_expect/warofrebellionco1473unit_meta.json, openlibrary/catalog/marc/tests/test_data/bin_expect/wrapped_lines.json, openlibrary/catalog/marc/tests/test_data/bin_expect/wwu_51323556.json, openlibrary/catalog/marc/tests/test_data/bin_expect/zweibchersatir01horauoft_meta.json, openlibrary/catalog/marc/tests/test_data/bin_input/cu31924091184469_meta.mrc, openlibrary/catalog/marc/tests/test_data/xml_expect/00schlgoog.json, openlibrary/catalog/marc/tests/test_data/xml_expect/0descriptionofta1682unit.json, openlibrary/catalog/marc/tests/test_data/xml_expect/13dipolarcycload00burk.json, openlibrary/catalog/marc/tests/test_data/xml_expect/1733mmoiresdel00vill.json, openlibrary/catalog/marc/tests/test_data/xml_expect/39002054008678_yale_edu.json, openlibrary/catalog/marc/tests/test_data/xml_expect/bijouorannualofl1828cole.json, openlibrary/catalog/marc/tests/test_data/xml_expect/cu31924091184469.json, openlibrary/catalog/marc/tests/test_data/xml_expect/engineercorpsofh00sher.json, openlibrary/catalog/marc/tests/test_data/xml_expect/flatlandromanceo00abbouoft.json, openlibrary/catalog/marc/tests/test_data/xml_expect/nybc200247.json, openlibrary/catalog/marc/tests/test_data/xml_expect/onquietcomedyint00brid.json, openlibrary/catalog/marc/tests/test_data/xml_expect/secretcodeofsucc00stjo.json, openlibrary/catalog/marc/tests/test_data/xml_expect/warofrebellionco1473unit.json, openlibrary/catalog/marc/tests/test_data/xml_expect/zweibchersatir01horauoft.json
  instance_internetarchive__openlibrary-2abe28b472ffed563a87cfe83685b161b35263b0-v13642507b4fc1f8d234172bf8129942da2c2ca26: openlibrary/tests/core/sample_amazon_record.py
  instance_internetarchive__openlibrary-2fe532a33635aab7a9bfea5d977f6a72b280a30c-v0f5aece3601a5b4419f7ccec1dbda2071be28ee4: openlibrary/tests/core/sample_amazon_record.py
  instance_internetarchive__openlibrary-3c48b4bb782189e0858e6c3fc7956046cf3e1cfb-v2d9a6c849c60ed19fd0858ce9e40b7cc8e097e59: openlibrary/catalog/marc/tests/test_data/bin_expect/equalsign_title.mrc, openlibrary/catalog/marc/tests/test_data/bin_expect/zweibchersatir01horauoft_meta.mrc, openlibrary/catalog/marc/tests/test_data/xml_expect/zweibchersatir01horauoft_marc.xml
  instance_internetarchive__openlibrary-7c8dc180281491ccaa1b4b43518506323750d1e4-v298a7a812ceed28c4c18355a091f1b268fe56d86: openlibrary/catalog/marc/tests/test_data/bin_expect/wrapped_lines.json
  instance_internetarchive__openlibrary-b67138b316b1e9c11df8a4a8391fe5cc8e75ff9f-ve8c8d62a2b60610a3c4631f5f23ed866bada9818: openlibrary/catalog/marc/tests/test_data/bin_expect/880_Nihon_no_chasho.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_alternate_script.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_arabic_french_many_linkages.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_publisher_unlinked.json, openlibrary/catalog/marc/tests/test_data/bin_expect/880_table_of_contents.json, openlibrary/catalog/marc/tests/test_data/bin_expect/bpl_0486266893.json, openlibrary/catalog/marc/tests/test_data/bin_expect/ithaca_two_856u.json, openlibrary/catalog/marc/tests/test_data/bin_input/880_Nihon_no_chasho.mrc, openlibrary/catalog/marc/tests/test_data/bin_input/880_alternate_script.mrc, openlibrary/catalog/marc/tests/test_data/bin_input/880_arabic_french_many_linkages.mrc, openlibrary/catalog/marc/tests/test_data/bin_input/880_publisher_unlinked.mrc, openlibrary/catalog/marc/tests/test_data/bin_input/880_table_of_contents.mrc, openlibrary/catalog/marc/tests/test_data/xml_expect/nybc200247.json, openlibrary/catalog/marc/tests/test_data/xml_expect/soilsurveyrepor00statgoog.json, openlibrary/catalog/marc/tests/test_data/xml_input/nybc200247_marc.xml
  instance_internetarchive__openlibrary-e8084193a895d8ee81200f49093389a3887479ce-ve8c8d62a2b60610a3c4631f5f23ed866bada9818: openlibrary/catalog/marc/tests/test_data/bin_expect/ithaca_two_856u.json

| source | touch test infrastructure |
|---|---|
| gold | 4 |
| agents | 0 |
| fixtures | 0 |
| test_patch, nested tests/ only (spec 20) | 10 |
```

- **gold**: the same four `config` hits as 17's report. No gold patch gains a `test-support` path.
- **agents**: 0 of 35 distinct recorded agent diffs touch test infrastructure. `runs/` is
  per-clone, so this count is in this report and not in the quick tier.
- **fixtures**: the script's fixture set is 17's (17 diffs). The test covers all 20 under
  `fixtures/{bad_patches,obsolescence,gate-integrity,agents}/`, and each splits byte-equal to
  18's split (invariant 4).
- **test_patch**: the 10 openlibrary rows, pinned by instance id in
  `test_ac3_the_test_patch_rows_the_rule_reaches`. The harness applies its own test patch, so
  ground truth is unaffected.

## AC8: the sealed bundle graded for real

Through `outcome task new --ask "$(cat ask.txt)" --checks fixtures/verifier/slugify/checks-sealed`
and `cli.run(task_id=T, patch_path=F)`, on the Mac's Colima (runc), in 96 s for all three:

| diff | verdict | reason | `slugify_sealed` | `keep_converters` |
|---|---|---|---|---|
| `gold.diff` | **PASS** | `all 1 valid hidden checks pass, no regressions` | fail → pass | pass → pass |
| `bad.diff` | **FAIL** | `1/1 valid hidden checks fail after change (over the 20% tolerance): slugify_sealed` | fail → fail (exit 0) | pass → pass |
| `keep-broken.diff` | **UNKNOWN**, route `regressed` | `1/1 valid hidden checks pass but 1 existing checks regressed: keep_converters` | fail → pass | pass → fail |

- `Evidence.sealed` is True on all three, and ground truth is `none`.
- `bad.diff`'s module exits 0 and prints its own list. Under spec 16 that exit status alone
  was a pass. Now its log reads `exit: 0` / `status: fail`.
- **Invariant 1, for real.** Before each of gold's two boxes is torn down, it is snapshotted
  and searched on the host for the value's four forms: the `/app` files git reads as modified
  or untracked, and all of `/workspace`, `/outcome_checks` and `/tmp`. Nothing is found. The
  positive control finds gold's `_NOT_ALNUM` in the after box's `slugify.py` and patch, and not
  in the before box. The search runs on the host, so it never puts the value into the box.

## The sealed fixture

`fixtures/verifier/slugify/checks-sealed/checks.json` has two checks:
- `slugify_sealed`: six inputs with non-ASCII letters written as `\u` escapes. Its
  `expected_stdout` is gold's `json.dumps` of their slugs, computed locally from `gold.diff`'s
  module and confirmed by AC8.
- `keep_converters`: byte-equal to `checks/`'s.

No slug occurs anywhere in the bundle outside `expected_stdout`, and there is no `files/`.
`bad.diff` differs on every input: it keeps case and accents.

## Decisions made in the build

- **The rule's shape.** `infra_kind`'s `test-support` line gains
  `NESTED_TEST_ROOTS.intersection(dirs[1:])`, so the table order is unchanged. `conftest.py`
  and config names under a nested `tests/` keep their own kind. `qutebrowser/tests.py` is a
  file, not a directory, and stays graded.
- **The comparison.** Exactly one trailing `"\n"` is removed from the observed stdout, so `\r\n`,
  two newlines or a leading newline all `fail`. The timeout test is `_status`'s: the exit code
  124/137 together with the sandbox's own note. A note with any other exit code moves nothing.
- **The `sealed:` line** is written only when the bundle has a sealed check, so every log of a
  bundle without one is byte-identical to spec 16's (invariant 3).
- **`Evidence.sealed`** is True when every `change` check has an `expected_stdout`. A `keep`
  check never carries one.
- **The lint's sources** are every check's `run` and `name`, and every file under `files/`
  read as UTF-8 with `surrogateescape`. A `name` admits only `[A-Za-z0-9._-]`, so only a bare
  value that fits one can leak into a name.

- **The scan reads `test_patch`, so `test_boundaries.py` exempts the script.** Spec 02's
  sweep bans grading-side field names from `scripts/`. The first VM suite failed on it, with 1
  failed out of 2277. The exemption covers one file, `gate_integrity_scan.py`, which draws
  nothing and prints paths. The sweep's non-vacuity test pins it: the script still names the
  field, and nothing in the package imports it. The SPEC's gap note records it as a third
  test edit.
- **No output cap.** `task_sandbox` is opened with no `output_limit`, so a sealed check's
  stdout reaches the host whole, and `MAX_EXPECTED_BYTES` bounds only the expectation.

## AC7: nothing else moves

- `core/verdict.py`'s sha256 is `d1fc610ae59ac1331cea950b67b3e9ca807cdd093262f478f4ce9be197f74cb6`,
  equal to 19's landing tree (`c2eb565`). `test_ac7_verdict_py_is_19s` pins it.
- On the Mac, `-k "not real"`, unmodified: `test_verifier`, `test_checks_control`,
  `test_sealed_checks`, `test_verdict`, `test_replay_equivalence`, `test_verdict_corpus`,
  `test_obsolescence`, `test_gate`, `test_landing_gate` and `test_race`. With
  `test_gate_integrity` and `test_in_process` (the two listed edits): **672 passed**.

## The suite (AC10)

`scripts/suite` on a gate VM (runsc, `OUTCOME_ISOLATION=required`), on the landing tree: origin/main
`c9b517f` (spec 19 and E10 landed) + 20.

```
2279 passed, 10 skipped, 11 warnings in 2271.87s (0:37:51)
green: gate 2279 passed, 10 skipped as expected
```

- There is no new expected skip: `tests/expected_skips/gate.txt` is unedited by 20.
- It runs in tens of minutes, against 19's 0:37:17.
- Two earlier runs were red on one test, `test_boundaries.py::test_ac4_only_task_loader_and_grading_mention_grading_data`
  (the scan's `test_patch`, see the build's decisions): a gate VM on 19's tree + 20 (2266 passed),
  and a gate VM on this landing tree (2278 passed). Nothing else failed in either run.
