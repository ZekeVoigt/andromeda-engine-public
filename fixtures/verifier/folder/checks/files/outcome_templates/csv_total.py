"""outcome template csv_total: the CSV at PATH's COLUMN sums to EXPECTED.

    python3 -I csv_total.py PATH COLUMN EXPECTED [--tolerance T]

Passes when every data row's COLUMN, stripped, parses as a finite Decimal, and
|sum - EXPECTED| <= T (default 0: exact). PATH is relative to the working directory (/app in
a check box), which is only ever read. Stdlib only, Python >= 3.8. Exit 0 pass, 1 fail,
2 bad arguments; one line on stdout.
"""

import argparse
import csv
import decimal
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


def number(text):
    try:
        value = decimal.Decimal(text.strip())
    except decimal.InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse(argv):
    p = Parser(prog="csv_total", add_help=False)
    p.add_argument("path", type=str)
    p.add_argument("column", type=str)
    p.add_argument("expected", type=str)
    p.add_argument("--tolerance", type=str, default="0")
    ns = p.parse_args(argv)
    relative(ns.path)
    ns.expected_value, ns.tolerance_value = number(ns.expected), number(ns.tolerance)
    if ns.expected_value is None:
        raise Usage("EXPECTED is not a finite decimal number: %r" % ns.expected)
    if ns.tolerance_value is None or ns.tolerance_value < 0:
        raise Usage("--tolerance is not a finite decimal number >= 0: %r" % ns.tolerance)
    return ns


def check(ns):
    header, rows = table(ns.path)
    if header is None:
        return False, rows
    if ns.column not in header:
        return False, "%s has no column %r" % (ns.path, ns.column)
    i = header.index(ns.column)
    with decimal.localcontext() as ctx:
        ctx.prec = 200
        total = decimal.Decimal(0)
        for n, row in enumerate(rows, 2):
            value = number(row[i]) if i < len(row) else None
            if value is None:
                return False, "%s row %d's %r is not a number: %r" % (
                    ns.path, n, ns.column, row[i] if i < len(row) else "")
            total += value
        off = abs(total - ns.expected_value)
    if off > ns.tolerance_value:
        return False, "%s's %r sums to %s over %d rows, not %s (tolerance %s)" % (
            ns.path, ns.column, total, len(rows), ns.expected, ns.tolerance)
    return True, "%s's %r sums to %s over %d rows (expected %s, tolerance %s)" % (
        ns.path, ns.column, total, len(rows), ns.expected, ns.tolerance)


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
