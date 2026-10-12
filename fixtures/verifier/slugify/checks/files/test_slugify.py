# -*- coding: utf-8 -*-
# The operator's own tests for spec 16's fixture: sealed with the task, run from
# /outcome_checks/, never copied into /app and never shown to the worker.
from ansible.module_utils.common.text.slugify import slugify


def test_lowercases_and_joins_words():
    assert slugify(u'Hello, World!') == u'hello-world'


def test_folds_accents():
    assert slugify(u'Café Déjà Vu') == u'cafe-deja-vu'


def test_strips_edges_and_collapses_runs():
    assert slugify(u'  --Ansible   2.10--  ') == u'ansible-2-10'


def test_custom_separator():
    assert slugify(u'a b c', separator=u'_') == u'a_b_c'
