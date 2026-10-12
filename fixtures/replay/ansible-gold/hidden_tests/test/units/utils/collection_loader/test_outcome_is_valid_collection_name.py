# -*- coding: utf-8 -*-
"""Acceptance tests: FQCN validation must reject Python keywords.

See change request: collection name validation must reject names where
either the namespace or the collection name is a Python reserved keyword,
and both segments must be valid Python identifiers under language rules.
"""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.utils.collection_loader import AnsibleCollectionRef


KEYWORD_NAMESPACE_NAMES = [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
    'class.foo',
    'for.bar',
]

KEYWORD_COLLECTION_NAMES = [
    'ns.def',
    'ns.return',
    'ns.assert',
    'ns.import',
    'ns.class',
    'ns.for',
]

VALID_NAMES = [
    'ns.coll',
    'my_namespace.my_collection',
    'foo.bar',
    'namespace123.collection123',
]


@pytest.mark.parametrize('name', KEYWORD_NAMESPACE_NAMES)
def test_is_valid_collection_name_rejects_keyword_namespace(name):
    assert AnsibleCollectionRef.is_valid_collection_name(name) is False


@pytest.mark.parametrize('name', KEYWORD_COLLECTION_NAMES)
def test_is_valid_collection_name_rejects_keyword_collection_name(name):
    assert AnsibleCollectionRef.is_valid_collection_name(name) is False


@pytest.mark.parametrize('name', VALID_NAMES)
def test_is_valid_collection_name_accepts_valid_names(name):
    assert AnsibleCollectionRef.is_valid_collection_name(name) is True


@pytest.mark.parametrize('name', KEYWORD_NAMESPACE_NAMES + KEYWORD_COLLECTION_NAMES + VALID_NAMES)
def test_is_valid_collection_name_returns_a_bool(name):
    # The validity check must return a boolean result rather than raising.
    result = AnsibleCollectionRef.is_valid_collection_name(name)
    assert isinstance(result, bool)


@pytest.mark.parametrize('name', KEYWORD_NAMESPACE_NAMES + KEYWORD_COLLECTION_NAMES)
def test_collectionref_constructor_rejects_keyword_segments(name):
    with pytest.raises(ValueError, match='invalid collection name'):
        AnsibleCollectionRef(name, '', 'resource', 'action')
