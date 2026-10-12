"""outcome template md_headings: the Markdown file at PATH has these headings.

    python3 -I md_headings.py PATH HEADING [HEADING ...] [--any-order]

Passes when each HEADING equals the text of an ATX heading line (`#` to `######`, the text
stripped and any closing `#`s dropped; case-sensitive; not inside a fenced code block), in
the order given unless --any-order. PATH is relative to the working directory (/app in a
check box), which is only ever read. Stdlib only, Python >= 3.8. Exit 0 pass, 1 fail, 2 bad
arguments; one line on stdout.
"""

import argparse
import os
import re
import stat
import sys

ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
CLOSING = re.compile(r"(?:^|[ \t]+)#+$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


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


def headings(text):
    out, fence = [], None
    for line in text.splitlines():
        m = FENCE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        m = ATX.match(line)
        if m:
            out.append(CLOSING.sub("", m.group(2) or "").strip())
    return out


def parse(argv):
    p = Parser(prog="md_headings", add_help=False)
    p.add_argument("path", type=str)
    p.add_argument("headings", nargs="+", metavar="HEADING")
    p.add_argument("--any-order", action="store_true")
    ns = p.parse_args(argv)
    relative(ns.path)
    if any(not h.strip() or h != h.strip() for h in ns.headings):
        raise Usage("a HEADING is empty or has surrounding whitespace")
    return ns


def check(ns):
    try:
        st = os.lstat(ns.path)
    except OSError:
        return False, ns.path + " does not exist"
    if not stat.S_ISREG(st.st_mode):
        return False, ns.path + " is not a regular file"
    try:
        with open(ns.path, "rb") as f:
            found = headings(f.read().decode("utf-8"))
    except UnicodeDecodeError as e:
        return False, "%s does not decode as UTF-8: %s" % (ns.path, e)
    if ns.any_order:
        missing = [h for h in ns.headings if h not in found]
        if missing:
            return False, "%s has no heading %s" % (ns.path, ", ".join(repr(h) for h in missing))
        return True, "%s has all %d headings" % (ns.path, len(ns.headings))
    at = 0
    for h in ns.headings:
        try:
            at = found.index(h, at) + 1
        except ValueError:
            return False, "%s has no heading %r after the ones before it" % (ns.path, h)
    return True, "%s has all %d headings, in order" % (ns.path, len(ns.headings))


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
