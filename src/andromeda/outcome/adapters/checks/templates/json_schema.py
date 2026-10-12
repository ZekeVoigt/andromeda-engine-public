"""outcome template json_schema: the JSON document at PATH matches SCHEMA_JSON.

    python3 -I json_schema.py PATH SCHEMA_JSON

SCHEMA_JSON is a JSON-Schema SUBSET: `type` (object, array, string, number, integer, boolean,
null), `properties`, `required`, `items`, `enum`, `minItems`, `maxItems` and
`additionalProperties: false`. Any other keyword is bad arguments (exit 2), so no constraint
is ever silently ignored. PATH is relative to the working directory (/app in a check box),
which is only ever read. Stdlib only, Python >= 3.8. Exit 0 pass, 1 fail, 2 bad arguments;
one line on stdout.
"""

import argparse
import json
import os
import stat
import sys

TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")
KEYWORDS = ("type", "properties", "required", "items", "enum", "minItems", "maxItems",
            "additionalProperties")


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


def count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def subset(schema, at="schema"):
    """Refuse (Usage) anything outside the subset, naming where."""
    if not isinstance(schema, dict):
        raise Usage("%s is not an object" % at)
    unknown = sorted(k for k in schema if k not in KEYWORDS)
    if unknown:
        raise Usage("%s uses %s, outside the subset (%s)" % (at, ", ".join(unknown), ", ".join(KEYWORDS)))
    if "type" in schema and schema["type"] not in TYPES:
        raise Usage("%s.type must be one of %s" % (at, ", ".join(TYPES)))
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise Usage("%s.properties is not an object" % at)
        for name, sub in schema["properties"].items():
            subset(sub, "%s.properties.%s" % (at, name))
    if "required" in schema and not (isinstance(schema["required"], list) and all(
            isinstance(r, str) for r in schema["required"])):
        raise Usage("%s.required is not a list of strings" % at)
    if "items" in schema:
        subset(schema["items"], at + ".items")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise Usage("%s.enum is not a list" % at)
    for k in ("minItems", "maxItems"):
        if k in schema and not count(schema[k]):
            raise Usage("%s.%s is not a non-negative integer" % (at, k))
    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        raise Usage("%s.additionalProperties may only be false" % at)


def is_type(value, name):
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return name == "number" or isinstance(value, int) or value.is_integer()


def same(a, b):
    """JSON equality: 1 and true differ, 1 and 1.0 do not."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b or (
        isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b)


def mismatch(value, schema, at="$"):
    """Why `value` does not match `schema`, or None when it does."""
    if "type" in schema and not is_type(value, schema["type"]):
        return "%s is not of type %s" % (at, schema["type"])
    if "enum" in schema and not any(same(value, e) for e in schema["enum"]):
        return "%s is not one of the enum" % at
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                return "%s has no required %r" % (at, name)
        props = schema.get("properties", {})
        for name, sub in props.items():
            if name in value:
                why = mismatch(value[name], sub, "%s.%s" % (at, name))
                if why:
                    return why
        if schema.get("additionalProperties") is False:
            extra = sorted(k for k in value if k not in props)
            if extra:
                return "%s has additional properties %s" % (at, ", ".join(repr(k) for k in extra))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return "%s has %d items, under minItems %d" % (at, len(value), schema["minItems"])
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return "%s has %d items, over maxItems %d" % (at, len(value), schema["maxItems"])
        if "items" in schema:
            for i, item in enumerate(value):
                why = mismatch(item, schema["items"], "%s[%d]" % (at, i))
                if why:
                    return why
    return None


def parse(argv):
    p = Parser(prog="json_schema", add_help=False)
    p.add_argument("path", type=str)
    p.add_argument("schema", type=str, metavar="SCHEMA_JSON")
    ns = p.parse_args(argv)
    relative(ns.path)
    try:
        ns.schema_value = json.loads(ns.schema)
    except ValueError as e:
        raise Usage("SCHEMA_JSON is not JSON: %s" % e)
    subset(ns.schema_value)
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
            value = json.loads(f.read().decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return False, "%s is not UTF-8 JSON: %s" % (ns.path, e)
    why = mismatch(value, ns.schema_value)
    if why:
        return False, "%s: %s" % (ns.path, why)
    return True, ns.path + " matches the schema"


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
