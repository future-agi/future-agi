"""Each version's download is the approved archive that version records.

The entry page offers "the approved files of version X in one archive" and
links each version's download. That link serves the bundle whose SHA-256, size,
file list and catalog digest are the ones the approved version records, for
every version, the latest included.
"""

import hashlib

import pytest
from rest_framework.test import APIClient

from omega_hub.services.bundles import (
    catalog_digest,
    file_list,
    manifest_of,
    read_canonical,
)
from omega_hub.tests.flows import publish

pytestmark = pytest.mark.django_db
ENTRY = "/omega-hub/v1/entries/future-agi/first-agent"


def test_each_version_downloads_the_archive_it_records(world):
    publish(world)
    publish(world, manifest={"version": "1.0.1"})
    visitor = APIClient()
    archives = {}
    for number in ("1.0.0", "1.0.1"):
        detail = visitor.get(f"{ENTRY}/versions/{number}")
        assert detail.status_code == 200, detail.content
        recorded = detail.json()
        download = visitor.get(f"{ENTRY}/versions/{number}/bundle?source=web")
        assert download.status_code == 200, download.content
        data = download.content
        assert hashlib.sha256(data).hexdigest() == recorded["bundleSha256"]
        assert download["X-Omega-Hub-Bundle-Sha256"] == recorded["bundleSha256"]
        assert len(data) == recorded["bundleBytes"]
        files = read_canonical(data)
        manifest = manifest_of(files)
        assert manifest["version"] == number
        assert file_list(files) == recorded["files"]
        assert catalog_digest(files, manifest) == recorded["digest"]
        archives[number] = data
    assert archives["1.0.0"] != archives["1.0.1"]
    # The page's own sentence is about the latest version: its download.
    latest = visitor.get(ENTRY).json()["latest"]
    assert latest["version"] == "1.0.1"
    assert latest["bundleSha256"] == hashlib.sha256(archives["1.0.1"]).hexdigest()
