"""The archive digest the entry page shows is the one the download carries.

Provenance on an entry page lists two digests: the digest of the approved
files, and the archive digest, the sha256 of the .tar.gz a visitor downloads.
The page reads the archive digest from the version's public shape
(``bundleSha256``), from the entry's latest version or from the version it is
pinned to. The download of that version names the same value in its
X-Omega-Hub-Bundle-Sha256 header, and its bytes hash to it, so a visitor can
check a download against the digest the page shows.
"""

import hashlib

import pytest
from rest_framework.test import APIClient

from omega_hub.api.serializers.cards import version_detail
from omega_hub.tests.flows import publish

pytestmark = pytest.mark.django_db
ENTRY = "/omega-hub/v1/entries/future-agi/first-agent"


def test_archive_digest_is_the_download_header_and_the_archive_sha256(world):
    version = publish(world).latest_version
    shape = version_detail(version)
    shown = shape["bundleSha256"]
    visitor = APIClient()
    download = visitor.get(f"{ENTRY}/versions/{version.version}/bundle?source=web")
    assert download.status_code == 200, download.content
    assert download["X-Omega-Hub-Bundle-Sha256"] == shown
    assert hashlib.sha256(download.content).hexdigest() == shown
    # Both places the entry page reads a version from carry the same value.
    page = visitor.get(ENTRY)
    assert page.status_code == 200, page.content
    assert page.json()["latest"]["bundleSha256"] == shown
    pinned = visitor.get(f"{ENTRY}/versions/{version.version}")
    assert pinned.status_code == 200, pinned.content
    assert pinned.json()["bundleSha256"] == shown
    # The files digest is another value: it covers the files, not the archive.
    assert shape["digest"] != f"sha256:{shown}"
