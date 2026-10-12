"""Spec 21: starter checks, not a check author -- `outcome checks templates|add`.

`add DIR TEMPLATE --name N [--expect change|keep] -- ARGS...` appends one check built from an
engine-shipped template to `DIR/checks.json` (creating it as `{"on": "inputs", "checks": []}`
when absent), and copies the template's script into `DIR/files/outcome_templates/`. From then
on the script is bundle bytes, sealed by `ref` like any other file, so a later release that
edits a template cannot move a sealed task's definition of done. `add` never seals: `task new
--checks DIR` does, unchanged.

ARGS are validated on the host by the template's own parser (each script's `parse`), plus the
rules below, before anything is written; every refusal exits 2 and leaves DIR byte-identical.
A template runs as `python3 -I /outcome_checks/outcome_templates/<t>.py ARGS` with cwd /app,
and only reads /app as data: nothing the diff wrote is imported or executed by it, so its exit
status is the engine's observation, not the diff's report (the SPEC's "Why templates are not
sealed by expected_stdout").

Sync and host-only: no container, no event loop, no model. Nothing here imports the broker,
the agent or a model client.
"""

import argparse
import hashlib
import importlib.util
import json
import shlex
import sys
from pathlib import Path

from pydantic import ValidationError

from andromeda.outcome.adapters.checks.bundle import (
    CHECKS_JSON, FILES, INPUTS, INPUTS_ON, CheckSpec)

__all__ = ["TEMPLATES", "TEMPLATE_DIR", "SCRIPTS", "Refused", "add", "main", "render", "script"]

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
SCRIPTS = "outcome_templates"           # DIR/files/<SCRIPTS>/, so /outcome_checks/<SCRIPTS>/
# name -> (parameters, what passes). The six, and nothing else, in the order they are listed.
TEMPLATES: dict[str, tuple[str, str]] = {
    "exists_parses": ("PATH --format csv|json|text",
                      "PATH is a regular, non-empty file that parses as the format"),
    "csv_no_blanks": ("PATH COLUMN...",
                      "the CSV has every COLUMN, a data row, and no row blank in any of them"),
    "csv_total": ("PATH COLUMN EXPECTED [--tolerance T]",
                  "every row's COLUMN is a number and they sum to EXPECTED, within T"),
    "json_schema": ("PATH SCHEMA_JSON",
                    "the JSON document matches a schema subset: type, properties, required, "
                    "items, enum, minItems, maxItems, additionalProperties: false"),
    "md_headings": ("PATH HEADING... [--any-order]",
                    "the Markdown has each ATX HEADING, in order unless --any-order"),
    "unchanged": ("PATH...",
                  "each input PATH keeps the sha256 it has in DIR/inputs/ (a keep check)"),
}


class Refused(Exception):
    """`add` would not write: the reason, and DIR is untouched."""


def script(name: str) -> Path:
    return TEMPLATE_DIR / f"{name}.py"


def _parser_of(name: str):
    """The template's own `parse(argv)`, loaded from its file under a private module name
    and never registered in sys.modules."""
    spec = importlib.util.spec_from_file_location(f"_outcome_template_{name}", script(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parsed(name: str, args: list[str]) -> None:
    """`Refused` unless template `name`'s own parser takes `args`."""
    module = _parser_of(name)
    try:
        module.parse(list(args))
    except module.Usage as e:
        raise Refused(f"{name}: bad arguments: {e}") from None


def render(name: str, args: list[str], check_name: str, expect: str | None = None) -> CheckSpec:
    """The CheckSpec `add` appends for template `name` run with `args`, validated on the host
    by the template's own parser; `Refused` naming the cause otherwise. `unchanged`'s ARGS are
    already `PATH=<sha256>` here (`add` pins them)."""
    if name not in TEMPLATES:
        raise Refused(f"unknown template {name!r}; `outcome checks templates` lists them: "
                      f"{', '.join(TEMPLATES)}")
    if expect is None:
        expect = "keep" if name == "unchanged" else "change"
    if name == "unchanged" and expect != "keep":
        raise Refused("unchanged is a keep check: its --expect may only be keep")
    _parsed(name, args)
    run = (f"python3 -I /outcome_checks/{SCRIPTS}/{name}.py "
           + " ".join(shlex.quote(a) for a in args))
    try:
        return CheckSpec(name=check_name, expect=expect, run=run)
    except ValidationError as e:
        raise Refused(f"check name {check_name!r}: {e.errors()[0]['msg']}") from None


def _pin(dir: Path, paths: list[str]) -> list[str]:
    """`unchanged`'s PATHs as `PATH=<sha256 of DIR/inputs/PATH>`. Each path's shape is the
    template's parser's to refuse first (absolute, `..`, `.git`); then it must be a regular
    file in DIR/inputs/."""
    _parsed("unchanged", [f"{path}={'0' * 64}" for path in paths])
    out = []
    for path in paths:
        source = dir / INPUTS / path
        if source.is_symlink() or not source.is_file():
            raise Refused(f"unchanged: {path} is not a file in {dir / INPUTS}/")
        out.append(f"{path}={hashlib.sha256(source.read_bytes()).hexdigest()}")
    return out


def add(dir: str | Path, name: str, check_name: str, expect: str | None,
        args: list[str]) -> CheckSpec:
    """Append template `name`'s check to DIR's bundle. Everything is validated before the
    first write; `Refused` leaves DIR byte-identical."""
    dir = Path(dir)
    if name == "unchanged":
        args = _pin(dir, list(args))
    spec = render(name, args, check_name, expect)
    doc_path = dir / CHECKS_JSON
    if doc_path.exists():
        try:
            doc = json.loads(doc_path.read_text())
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise Refused(f"{doc_path}: cannot read {CHECKS_JSON}: {e}") from None
        if not (isinstance(doc, dict) and isinstance(doc.get("checks"), list)):
            raise Refused(f"{doc_path}: {CHECKS_JSON} is not an object with a checks list")
    else:
        doc = {"on": INPUTS_ON, "checks": []}
    names = [c.get("name") for c in doc["checks"] if isinstance(c, dict)]
    if check_name in names:
        raise Refused(f"{doc_path}: a check is already named {check_name!r}")
    target = dir / FILES / SCRIPTS / f"{name}.py"
    for parent in (dir, dir / FILES, target.parent):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise Refused(f"{parent} is not a directory")
    source = script(name).read_bytes()
    if target.exists() and (target.is_symlink() or not target.is_file()
                            or target.read_bytes() != source):
        raise Refused(f"{target} exists with other bytes; it is never overwritten")
    doc["checks"].append(spec.model_dump(exclude_none=True, exclude_defaults=True))
    text = json.dumps(doc, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(source)
    doc_path.write_text(text)
    return spec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="outcome checks",
        description="starter checks for a `checks` bundle: engine-shipped templates, copied "
                    "into the bundle and sealed with it by `outcome task new --checks DIR`. "
                    "They check what a program can; they do not author checks.")
    sub = parser.add_subparsers(dest="op", required=True)
    sub.add_parser("templates", help="list the six templates, their parameters and purposes")
    add_p = sub.add_parser("add", help="append a template's check to DIR/checks.json and copy "
                                       "its script into DIR/files/outcome_templates/")
    add_p.add_argument("dir", type=Path, metavar="DIR")
    add_p.add_argument("template", metavar="TEMPLATE")
    add_p.add_argument("--name", required=True, help="the check's name, unique in DIR")
    add_p.add_argument("--expect", choices=["change", "keep"], default=None,
                       help="default change; unchanged's default and only value is keep")
    add_p.epilog = ("the template's arguments follow `--`: "
                    "outcome checks add DIR TEMPLATE --name N [--expect E] -- ARGS...; "
                    "paths in them are relative to /app")
    return parser


def main(argv: list[str]) -> int:
    """`outcome checks ...`: 0 done, 2 refused (usage included) with DIR untouched."""
    argv = list(argv)
    # ARGS are the template's, after the first `--`, and never parsed as `add`'s own flags
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    args = build_parser().parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    if args.op == "templates":
        width = max(len(n) for n in TEMPLATES)
        for name, (params, purpose) in TEMPLATES.items():
            print(f"{name:<{width}}  {params}\n{'':<{width}}  {purpose}")
        return 0
    try:
        spec = add(args.dir, args.template, args.name, args.expect, rest)
    except Refused as e:
        print(f"outcome checks add: refused: {e}", file=sys.stderr)
        return 2
    print(f"added {spec.name} ({spec.expect}): {spec.run}")
    return 0
