"""The HUB-19 restore drill through the Hub's own read path (fix round 4).

The live drill on the cloud rig deleted an approved bundle and saw the public download answer a raw
500 (FileNotFoundError) until the restore. A missing object is now a typed 503 with Retry-After.
"""

from __future__ import annotations

import io

import pytest
from minio.error import S3Error

from omega_hub.services import storage


def s3_error(code: str, message: str = "") -> S3Error:
    return S3Error(None, code, message or code, "/omega-hub", "req", "host")


@pytest.mark.django_db
def test_the_restore_drill_through_the_public_download(world):
    # HUB-19 on a listed version: delete only its bundle, the download answers a typed 503 (the live
    # drill on the cloud rig saw a raw 500 here), restore the recorded version, the pin downloads again.
    from django.core.management import call_command

    from omega_hub.tests.flows import publish

    entry = publish(world)
    version = entry.latest_version
    url = f"/omega-hub/v1/entries/{entry.publisher.handle}/{entry.name}/versions/{version.version}/bundle"
    before = world.b.get(url)
    assert before.status_code == 200 and storage.sha256_hex(before.content) == version.bundle_sha256
    store = storage.get_store()
    recorded = store.versions(version.bundle_key)[0]
    store.delete(version.bundle_key)
    missing = world.b.get(url)
    assert missing.status_code == 503, missing.content
    assert missing.json()["error"]["code"] == "OMEGA_HUB_OBJECT_MISSING" and missing["Retry-After"] == "300"
    call_command("omega_hub_storage", "restore", "--key", version.bundle_key, "--version", recorded, stdout=io.StringIO())
    after = world.b.get(url)
    assert after.status_code == 200 and storage.sha256_hex(after.content) == version.bundle_sha256


def test_only_a_missing_object_becomes_a_503(monkeypatch):
    class Refusing:
        def open(self, key, version_id=None):
            raise s3_error("AccessDenied")

    monkeypatch.setattr(storage, "get_store", lambda: Refusing())
    with pytest.raises(S3Error):
        storage.read_bytes("bundles/" + "a" * 64 + ".tar.gz")
