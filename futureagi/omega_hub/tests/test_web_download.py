"""The in-app Download is uncounted until a separate web source is migrated."""

import pytest
from rest_framework.test import APIClient

from omega_hub.models import DownloadDaily, Version
from omega_hub.services import storage
from omega_hub.tests.flows import publish

pytestmark = pytest.mark.django_db
BUNDLE = "/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/bundle"


@pytest.mark.parametrize("signed_in", [False, True])
@pytest.mark.parametrize("yanked", [False, True])
def test_exact_frontend_download_serves_bytes_and_headers_without_counting(
    world, signed_in, yanked
):
    entry = publish(world)
    version = entry.latest_version
    if yanked:
        version.status = Version.Status.YANKED
        version.yanked_reason = "Replaced\nUse the next version"
        version.save(update_fields=["status", "yanked_reason"])
    client = world.c if signed_in else APIClient()
    response = client.get(BUNDLE + "?source=web")
    assert response.status_code == 200, response.content
    assert response.content == storage.read_bytes(version.bundle_key)
    assert response["Content-Type"] == "application/gzip"
    assert response["X-Omega-Hub-Digest"] == version.digest
    assert response["X-Omega-Hub-Bundle-Sha256"] == version.bundle_sha256
    assert response["Cache-Control"] == "no-store"
    assert (
        response["Content-Disposition"]
        == 'attachment; filename="first-agent-1.0.0.tar.gz"'
    )
    if yanked:
        assert response["X-Omega-Hub-Yanked"] == "Replaced Use the next version"
    else:
        assert "X-Omega-Hub-Yanked" not in response
    assert not DownloadDaily.objects.exists()
    # A web download also must not consume the address's CLI count budget or dedupe slot.
    cli = client.get(BUNDLE)
    assert cli.status_code == 200 and cli.content == response.content
    row = DownloadDaily.objects.get()
    assert row.source == "cli" and row.count == 1


def test_absent_source_still_counts_as_cli(world):
    publish(world)
    response = APIClient().get(BUNDLE)
    assert response.status_code == 200, response.content
    row = DownloadDaily.objects.get()
    assert row.source == "cli" and row.count == 1


@pytest.mark.parametrize("source", ["bogus", "", "WEB"])
def test_unknown_sources_stay_400_and_uncounted(world, source):
    publish(world)
    response = APIClient().get(BUNDLE, {"source": source})
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == "OMEGA_HUB_INVALID"
    assert not DownloadDaily.objects.exists()


@pytest.mark.parametrize("source", ["cli", "studio", "deploy", "console"])
def test_existing_sources_keep_their_counts_for_sessions(world, source):
    publish(world)
    response = world.c.get(BUNDLE, {"source": source})
    assert response.status_code == 200, response.content
    row = DownloadDaily.objects.get()
    assert row.source == source and row.count == 1
