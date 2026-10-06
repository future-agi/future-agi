"""GET /api/edition/: data for Settings > Plan & License (TH-8084).

Self-hosted only (Cloud answers {"edition": "cloud"}). Admins see licence
details with a masked licence id and a key fingerprint; nobody ever gets the
raw licence key, in the body or in the logs.
"""

from __future__ import annotations

import hashlib
import logging

import pytest

from tfc.capabilities import edition
from tfc.capabilities.tests.edition_factories import make_member, make_org

try:
    from ee.licensing.tests.fixtures import (  # noqa: F401
        install_license,
        test_signing_keypair,
        token_for_state,
    )
except ImportError:  # OSS lane: licence cases carry requires_ee and are skipped
    pass

pytestmark = [pytest.mark.edition_rule, pytest.mark.django_db]

URL = "/api/edition/"


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)


@pytest.fixture
def community(self_hosted, monkeypatch):
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)


def _member_client(organization, workspace):
    from conftest import WorkspaceAwareAPIClient

    member = make_member(organization)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    return client


def test_requires_authentication(api_client):
    """AC-16: the edition endpoint is not public."""
    assert api_client.get(URL).status_code in (401, 403)


def test_community_admin_view(community, auth_client, organization):
    """AC-01 / AC-02: Community · Self-hosted with 1 / 1 / 3 and current usage."""
    make_member(organization)
    body = auth_client.get(URL).json()["result"]

    assert body["edition"] == "community"
    assert body["deployment"] == "self_hosted"
    assert body["limits"] == {
        "organizations": {"limit": 1, "current": 1},
        "workspaces": {"limit": 1, "current": 1},
        "members": {"limit": 3, "current": 2},
    }
    assert body["over_limit"] is False
    assert body["contact"] == "sales@futureagi.com"
    assert body["activation"] == {"method": "env_restart"}
    assert set(body["enterprise_features"]) >= {
        "falcon_ai",
        "turing_models",
        "protect",
        "error_feed",
        "members",
        "organizations",
        "workspaces",
    }
    assert body["license"]["state"] in ("missing", "invalid")
    assert body["license"]["license_id_masked"] is None


def test_over_limit_is_flagged(community, auth_client):
    """AC-11: an install over Community limits says so; nothing is hidden."""
    make_org()
    body = auth_client.get(URL).json()["result"]
    assert body["over_limit"] is True
    assert body["limits"]["organizations"]["current"] == 2


def test_non_admin_gets_no_licence_block(community, organization, workspace):
    """AC-16: members below admin see edition and limits only."""
    client = _member_client(organization, workspace)
    try:
        body = client.get(URL).json()["result"]
    finally:
        client.stop_workspace_injection()
    assert body["edition"] == "community"
    assert "limits" in body
    assert "license" not in body


def test_cloud_answers_cloud_only(edition_cloud, auth_client):
    """AC-02 / AC-13: Cloud never shows the self-hosted page data."""
    assert auth_client.get(URL).json()["result"] == {"edition": "cloud"}


@pytest.mark.requires_ee
def test_enterprise_admin_view_masks_the_licence(
    self_hosted, install_license, test_signing_keypair, auth_client, caplog, settings
):
    """AC-10 / AC-16: licence id masked, key only as a fingerprint, never raw."""
    snapshot = install_license("active")
    raw_key = token_for_state("active", test_signing_keypair[0])
    settings.EE_LICENSE_KEY = raw_key

    with caplog.at_level(logging.DEBUG):
        response = auth_client.get(URL)
    body = response.json()["result"]

    assert body["edition"] == "enterprise"
    assert body["limits"]["members"]["limit"] is None
    licence = body["license"]
    assert licence["state"] == "active"
    assert licence["license_type"] == "production"
    assert licence["issued_to"] == snapshot.issued_to
    assert licence["license_id_masked"] == "lic_****9f2a"
    assert (
        licence["key_fingerprint"] == hashlib.sha256(raw_key.encode()).hexdigest()[:8]
    )
    assert raw_key not in response.content.decode()
    assert snapshot.license_id not in response.content.decode()
    assert raw_key not in caplog.text


@pytest.mark.requires_ee
def test_expired_licence_is_community_with_state(
    self_hosted, install_license, auth_client
):
    """AC-17: an expired licence shows Community plus the licence state."""
    install_license("expired")
    body = auth_client.get(URL).json()["result"]
    assert body["edition"] == "community"
    assert body["license"]["state"] == "expired"
    assert body["limits"]["organizations"]["limit"] == 1
