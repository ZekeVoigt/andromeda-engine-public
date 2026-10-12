# -*- coding: utf-8 -*-
"""Acceptance tests: ansible-galaxy must reject collection names containing
Python reserved keywords, through the public validate_collection_name()
helper used by the ansible-galaxy CLI argument parser.
"""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.errors import AnsibleError
from ansible.galaxy import collection


KEYWORD_NAMES = [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
    'ns.def',
    'ns.import',
]

VALID_NAMES = [
    'ns.coll',
    'my_namespace.my_collection',
]


@pytest.mark.parametrize('name', KEYWORD_NAMES)
def test_validate_collection_name_rejects_python_keywords(name):
    with pytest.raises(AnsibleError):
        collection.validate_collection_name(name)


@pytest.mark.parametrize('name', KEYWORD_NAMES)
def test_validate_collection_name_rejects_python_keywords_with_version_range(name):
    # A version specifier suffix must not bypass the keyword check.
    with pytest.raises(AnsibleError):
        collection.validate_collection_name(name + ':1.0.0')


@pytest.mark.parametrize('name', VALID_NAMES)
def test_validate_collection_name_accepts_valid_names(name):
    assert collection.validate_collection_name(name) == name
