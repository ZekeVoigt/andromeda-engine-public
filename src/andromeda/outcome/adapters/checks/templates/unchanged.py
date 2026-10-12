"""outcome template unchanged: each PATH still has the sha256 it had in the inputs.

    python3 -I unchanged.py PATH=<sha256> [PATH=<sha256> ...]

`outcome checks add DIR unchanged -- PATH ...` reads each PATH from DIR/inputs/ and writes its
sha256 into these arguments, so a sealed task's inputs are pinned by their bytes. It is a
`keep` check: a worker that edits an input it names regresses. PATH is relative to the
working directory (/app in a check box), which is only ever read. Stdlib only, Python >= 3.8.
Exit 0 pass, 1 fail, 2 bad arguments; one line on stdout.
"""

import argparse
import hashlib
import os
import re
import stat
import sys

PINNED = re.compile(r"^(.+)=([0-9a-f]{64})$")


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
    p = Parser(prog="unchanged", add_help=False)
    p.add_argument("pins", nargs="+", metavar="PATH=SHA256")
    ns = p.parse_args(argv)
    ns.pinned = []
    for pin in ns.pins:
        m = PINNED.match(pin)
        if not m:
            raise Usage("not PATH=<sha256 hex>: %r" % pin)
        ns.pinned.append((relative(m.group(1)), m.group(2)))
    if len({path for path, _ in ns.pinned}) != len(ns.pinned):
        raise Usage("a PATH is named twice")
    return ns


def check(ns):
    for path, want in ns.pinned:
        try:
            st = os.lstat(path)
        except OSError:
            return False, path + " does not exist"
        if not stat.S_ISREG(st.st_mode):
            return False, path + " is not a regular file"
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != want:
            return False, "%s changed: sha256 %s, not %s" % (path, h.hexdigest(), want)
    return True, "%d input(s) unchanged: %s" % (
        len(ns.pinned), ", ".join(path for path, _ in ns.pinned))


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
