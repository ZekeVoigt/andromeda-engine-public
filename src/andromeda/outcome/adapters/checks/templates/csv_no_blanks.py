"""outcome template csv_no_blanks: no data row of the CSV at PATH is blank in any COLUMN.

    python3 -I csv_no_blanks.py PATH COLUMN [COLUMN ...]

Passes when the header holds every COLUMN, there is at least one data row, and no data row's
value in any of them is empty after strip() (a row too short to reach one is blank there).
PATH is relative to the working directory (/app in a check box), which is only ever read.
Stdlib only, Python >= 3.8. Exit 0 pass, 1 fail, 2 bad arguments; one line on stdout.
"""

import argparse
import csv
import io
import os
import stat
import sys


class Usage(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Usage(message)


def relative(path):
    parts = path.replace("\\", "/").split("/")
    if (not path or path.startswith("/") or ".." in parts
            or any(p.startswith(".git") for p in parts)):
        raise Usage("PATH must be relative to /app, with no '..' and no .git component: "
                    + repr(path))
    return path


def table(path):
    """(header, data rows) of the CSV file at `path`, or (None, why) when it cannot be read.
    A wholly empty line is no row, as `csv.DictReader` reads it."""
    try:
        st = os.lstat(path)
    except OSError:
        return None, path + " does not exist"
    if not stat.S_ISREG(st.st_mode):
        return None, path + " is not a regular file"
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
        rows = [row for row in csv.reader(io.StringIO(text, newline="")) if row]
    except (UnicodeDecodeError, csv.Error) as e:
        return None, "%s does not parse as UTF-8 CSV: %s" % (path, e)
    if not rows or not rows[0]:
        return None, path + " has no header row"
    return rows[0], rows[1:]


def parse(argv):
    p = Parser(prog="csv_no_blanks", add_help=False)
    p.add_argument("path", type=str)
    p.add_argument("columns", nargs="+", metavar="COLUMN")
    ns = p.parse_args(argv)
    relative(ns.path)
    if len(set(ns.columns)) != len(ns.columns):
        raise Usage("a COLUMN is named twice")
    return ns


def check(ns):
    header, rows = table(ns.path)
    if header is None:
        return False, rows
    missing = [c for c in ns.columns if c not in header]
    if missing:
        return False, "%s has no column %s" % (ns.path, ", ".join(repr(c) for c in missing))
    if not rows:
        return False, ns.path + " has no data row"
    for n, row in enumerate(rows, 2):
        for c in ns.columns:
            i = header.index(c)
            if i >= len(row) or not row[i].strip():
                return False, "%s row %d is blank in %r" % (ns.path, n, c)
    return True, "%s: %d rows, none blank in %s" % (
        ns.path, len(rows), ", ".join(repr(c) for c in ns.columns))


def main(argv):
    try:
        ns = parse(argv)
    except Usage as e:
        print("bad arguments: %s" % e)
        return 2
    ok, line = check(ns)
    print(("pass: " if ok else "fail: ") + line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
