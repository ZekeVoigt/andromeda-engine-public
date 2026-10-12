"""Spec 19: sealed oracles -- observers in the box, expectations never.

A sealed oracle's hidden modules (observers) call `observe(key, value)` instead of asserting,
and the values a correct change produces sit beside them on the host, in `expected/`. After a
side runs, the observations file comes back with the logs and the HOST judges each key: pass
only when it was observed and every observation of it equals its expectation, as canonical
JSON. Nothing in the box is trusted, so nothing here reads what the box reported about itself.

Pure, except `load`, which reads the expectations files. No env var, no clock, no randomness
(invariant 7). Both graders call `judge` through the module, so a test can replace it.
"""

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from andromeda.outcome.adapters.coding.docker import InfraError
from andromeda.outcome.core.types import Status

OBSERVATIONS = "/workspace/outcome_observations.jsonl"
HELPER_MODULE = "outcome_seal"
LEAK_MIN_BYTES = 8
MAX_OBSERVATION_BYTES = 16 * 1024 * 1024
KEY = re.compile(r"[A-Za-z0-9_.:/\[\]=,-]{1,160}")
# A draw's caches: a writer that ran its observers leaves them, and they are nobody's expectations.
JUNK_DIRS = ("__pycache__", ".pytest_cache")

# The module the observers import, planted for each grading (and in the writer's box), never
# stored with an oracle. Stdlib only: it runs under whatever interpreter the task's runner has.
# One os.write per line on an O_APPEND fd, so xdist workers' lines never interleave.
HELPER_SOURCE = f'''"""{HELPER_MODULE}: planted by the grading engine (spec 19). It records; it judges nothing."""

import json as _json
import os as _os

PATH = {OBSERVATIONS!r}


def _append(line):
    fd = _os.open(PATH, _os.O_WRONLY | _os.O_APPEND | _os.O_CREAT, 0o666)
    try:
        _os.write(fd, (line + "\\n").encode("utf-8"))
    finally:
        _os.close(fd)


def _record(key, value, of):
    try:
        line = _json.dumps({{"key": key, "value": value}}, allow_nan=False)
    except (TypeError, ValueError):
        line = _json.dumps({{"key": key, "error": type(of).__name__}})
    _append(line)


def observe(key, value):
    """Record one observation of `key`: `value` as JSON data, or the name of its type."""
    _record(key, value, value)


def observe_call(key, fn, *args, **kwargs):
    """Call `fn` and record {{"returned": <value>}} or {{"raised": "<exception class name>"}}."""
    try:
        value = fn(*args, **kwargs)
    except Exception as e:
        _record(key, {{"raised": type(e).__name__}}, e)
        return None
    _record(key, {{"returned": value}}, value)
    return value
'''


class SealError(InfraError):
    """An unusable sealed oracle: an orphan, a malformed expectations file, a bad key."""


# observer path (repo-relative) -> key -> the value a correct change produces
Expectations = dict[str, dict[str, Any]]


def canonical(value: Any) -> str:
    """The one form two values are compared in: `1` is not `1.0`, and `true` is not `1`."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def strength(value: Any) -> Literal["strong", "weak"]:
    """Weak: a bool, None, or a canonical form under LEAK_MIN_BYTES -- guessable, never lintable."""
    if value is None or isinstance(value, bool) or len(canonical(value).encode()) < LEAK_MIN_BYTES:
        return "weak"
    return "strong"


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON data")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"key {k!r} repeated")
        out[k] = v
    return out


def expectations_path(observer: str) -> str:
    """`<dir>/test_outcome_x.py` -> `<dir>/test_outcome_x.json`, both repo-relative."""
    return PurePosixPath(observer).with_suffix(".json").as_posix()


def expectation_files(expected_root: Path) -> list[str]:
    """Every `*.json` under `expected_root`, root-relative, caches excluded, sorted."""
    if not expected_root.is_dir():
        return []
    return sorted(p.relative_to(expected_root).as_posix() for p in expected_root.rglob("*.json")
                  if p.is_file() and not any(part in JUNK_DIRS for part in p.relative_to(expected_root).parts))


def _load(expected_root: str | Path, hidden_rel: list[str]) -> Expectations:
    """Pair each observer with its expectations file and read them, or raise SealError."""
    expected_root = Path(expected_root)
    wanted = {expectations_path(rel): rel for rel in hidden_rel}
    on_disk = set(expectation_files(expected_root))
    problems = [f"observer {wanted[j]} has no expectations file {j}" for j in sorted(set(wanted) - on_disk)]
    problems += [f"expectations file {j} has no observer" for j in sorted(on_disk - set(wanted))]
    if problems:
        raise SealError("sealed oracle: " + "; ".join(problems))
    out: Expectations = {}
    owner: dict[str, str] = {}
    for j, rel in sorted(wanted.items()):
        try:
            data = json.loads((expected_root / j).read_text(), parse_constant=_no_constant,
                              object_pairs_hook=_pairs)
        except (ValueError, UnicodeDecodeError) as e:
            raise SealError(f"sealed oracle: {j}: not JSON data: {e}") from e
        if not isinstance(data, dict) or not data:
            raise SealError(f"sealed oracle: {j}: not a non-empty JSON object")
        for key, value in data.items():
            if not KEY.fullmatch(key):
                raise SealError(f"sealed oracle: {j}: key {key!r} breaks the grammar {KEY.pattern}")
            if key in owner:
                raise SealError(f"sealed oracle: key {key!r} is in both {owner[key]} and {rel}")
            owner[key] = rel
            try:
                canonical(value)
            except ValueError as e:
                raise SealError(f"sealed oracle: {j}: {key}: not JSON data: {e}") from e
        out[rel] = data
    return out


# `seal.load` is the interface. Bound, not defined under that name: tests/test_boundaries.py
# pins the one module-level `def load` in the engine (runtime/resident.py), so that any other
# bare call of that name stays the grading loader it forbids.
load = _load


def names(exp: Expectations) -> list[str]:
    """Every expected key as a check name, `<observer>::<key>`, sorted."""
    return sorted(f"{rel}::{key}" for rel, keys in exp.items() for key in keys)


def lint(exp: Expectations, sources: dict[str, str]) -> list[str]:
    """The strong keys whose value occurs verbatim in a source or a key: `<observer>::<key>`, sorted."""
    haystacks = [*sources.values(), *(key for keys in exp.values() for key in keys)]
    leaked = []
    for rel, keys in exp.items():
        for key, value in keys.items():
            if strength(value) == "weak":
                continue
            forms = {canonical(value), json.dumps(value), repr(value)}
            if isinstance(value, str):
                forms.add(value)
            if any(form in text for form in forms for text in haystacks):
                leaked.append(f"{rel}::{key}")
    return sorted(leaked)


def without(exp: Expectations, leaked: list[str]) -> Expectations:
    """`exp` with the named keys dropped. An observer left with no key stays, keyed to nothing:
    it still runs, and what the runner reports about it is still discarded, never a check."""
    gone = set(leaked)
    return {rel: {k: v for k, v in keys.items() if f"{rel}::{k}" not in gone} for rel, keys in exp.items()}


def _observations(text: str) -> tuple[list[tuple[str, str, Any]], int]:
    """([(key, "value" | "error", payload)], junk): only the helper's two line shapes count."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out, junk = [], 0
    for line in lines:
        try:
            obj = json.loads(line, parse_constant=_no_constant)
            canonical(obj)  # `1e400` parses to inf without parse_constant: not JSON data either
        except (ValueError, RecursionError):  # a forged line may nest past the parser's depth
            junk += 1
            continue
        if (isinstance(obj, dict) and isinstance(obj.get("key"), str)
                and (set(obj) == {"key", "value"}
                     or (set(obj) == {"key", "error"} and isinstance(obj["error"], str)))):
            shape = "value" if "value" in obj else "error"
            out.append((obj["key"], shape, obj[shape]))
        else:
            junk += 1
    return out, junk


def judge_summary(exp: Expectations, observations: str) -> dict[str, Any]:
    """`judge`'s statuses plus the line counts, for `<label>_seal.json`."""
    parsed, junk = _observations(observations)
    by_key: dict[str, list[tuple[str, Any]]] = {}
    for key, shape, payload in parsed:
        by_key.setdefault(key, []).append((shape, payload))
    expected_keys = {key for keys in exp.values() for key in keys}
    statuses: dict[str, Status] = {}
    for rel, keys in sorted(exp.items()):
        for key, want in sorted(keys.items()):
            seen = by_key.get(key, [])
            values = [canonical(p) for shape, p in seen if shape == "value"]
            if any(v != canonical(want) for v in values):
                status: Status = "fail"
            elif any(shape == "error" for shape, _ in seen):
                status = "error"
            else:
                status = "pass" if values else "fail"
            statuses[f"{rel}::{key}"] = status
    return {"statuses": statuses, "lines": len(parsed) + junk, "junk": junk,
            "unexpected": sum(key not in expected_keys for key, _, _ in parsed)}


def judge(exp: Expectations, observations: str) -> dict[str, Status]:
    """One status per expected key, `<observer>::<key>`: a function of `exp` and these bytes alone."""
    return judge_summary(exp, observations)["statuses"]
