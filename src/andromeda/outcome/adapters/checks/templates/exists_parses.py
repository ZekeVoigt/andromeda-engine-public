"""outcome template exists_parses: PATH is a regular, non-empty file that parses as FORMAT.

    python3 -I exists_parses.py PATH --format csv|json|text

csv: it parses with `csv`, has a header, and every row has the header's field count (a wholly
empty line is no row, as `csv.DictReader` reads it).
json: it is one JSON document. text: it decodes as UTF-8. PATH is relative to the working
directory (/app in a check box), which is only ever read. Stdlib only, Python >= 3.8.
Exit 0 pass, 1 fail, 2 bad arguments; one line on stdout either way.
"""

import argparse
import csv
import io
import json
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


def parse(argv):
    p = Parser(prog="exists_parses", add_help=False)
    p.add_argument("path", type=str)
    p.add_argument("--format", required=True, choices=["csv", "json", "text"])
    ns = p.parse_args(argv)
    relative(ns.path)
    return ns


def check(ns):
    try:
        st = os.lstat(ns.path)
    except OSError:
        return False, ns.path + " does not exist"
    if not stat.S_ISREG(st.st_mode):
        return False, ns.path + " is not a regular file"
    if st.st_size == 0:
        return False, ns.path + " is empty"
    with open(ns.path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, "%s does not decode as UTF-8: %s" % (ns.path, e)
    if ns.format == "text":
        return True, "%s is UTF-8 text (%d bytes)" % (ns.path, len(raw))
    if ns.format == "json":
        try:
            json.loads(text)
        except ValueError as e:
            return False, "%s is not JSON: %s" % (ns.path, e)
        return True, ns.path + " parses as JSON"
    try:
        rows = [row for row in csv.reader(io.StringIO(text, newline="")) if row]
    except csv.Error as e:
        return False, "%s does not parse as CSV: %s" % (ns.path, e)
    if not rows or not rows[0]:
        return False, ns.path + " has no header row"
    width = len(rows[0])
    for n, row in enumerate(rows[1:], 2):
        if len(row) != width:
            return False, "%s row %d has %d fields, the header %d" % (ns.path, n, len(row), width)
    return True, "%s parses as CSV: a %d-field header and %d rows" % (ns.path, width, len(rows) - 1)


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
