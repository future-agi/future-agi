"""An environment's actor limits, actor models and uploaded knowledge, written into its draft (plan §5.3, S14)."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from simulate.services.harness_versioning import drafts
from simulate.tests.test_harness_actors import BASE, MANAGER, _add, _built
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _draft_actors(environment):
    raw = drafts.edit_sides(environment)["environment"].get("actors.json")
    return json.loads(raw) if raw else {}


def test_limits_are_settings_kept_beside_the_cast_and_none_is_no_limit(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-limits")
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    put = auth_client.put(
        f"{BASE}/{environment.id}/actor-limits/",
        {
            "limits": {
                "max_active": 3,
                "actor_seconds_per_call": None,
                "model": "gemini-3.7-flash",
            },
            "expected_revision": 1,
        },
        format="json",
    )
    assert put.status_code == 200, put.content
    body = put.json()
    assert body["limits"] == {
        "max_active": 3,
        "actor_seconds_per_call": None,
        "model": "gemini-3.7-flash",
    }
    assert body["actor_models"][0] == "gemini-3.8-flash"
    # An edit to an actor afterwards keeps the limits.
    edited = auth_client.put(
        f"{BASE}/{environment.id}/actors/manager/",
        {"actor": {**MANAGER, "goal": "Hold the price"}, "expected_revision": 2},
        format="json",
    )
    assert edited.status_code == 200, edited.content
    assert _draft_actors(environment)["limits"]["max_active"] == 3


@pytest.mark.parametrize(
    "limits",
    [
        {"max_active": 0},
        {"max_active": "two"},
        {"model": "some-other-model"},
        {"loop_guard_events": 6},
    ],
)
def test_a_limit_that_is_not_a_positive_number_or_a_known_model_is_refused(
    user, workspace, storage, auth_client, limits
):
    environment = _built(user, workspace, "actor-limits-bad")
    put = auth_client.put(
        f"{BASE}/{environment.id}/actor-limits/", {"limits": limits}, format="json"
    )
    assert put.status_code == 400 and put.json()["error"] == "limits_invalid"


def test_an_uploaded_document_is_offered_to_actors_and_kept_in_the_draft(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-upload")
    upload = auth_client.post(
        f"{BASE}/{environment.id}/files/",
        {
            "file": SimpleUploadedFile(
                "pricing policy.md", b"Discounts above 15% need the regional manager."
            ),
            "kind": "knowledge",
        },
        format="multipart",
    )
    assert upload.status_code == 200, upload.content
    assert "knowledge/pricing policy.md" in upload.json()["knowledge_sources"]["files"]
    files = drafts.edit_sides(environment)["environment"]
    assert files["knowledge/pricing policy.md"].startswith(b"Discounts above 15%")


@pytest.mark.parametrize(
    "name, content, kind",
    [
        ("notes.pdf", b"x", "knowledge"),
        ("data.json", b"{not json", "knowledge"),
        ("empty.md", b"", "knowledge"),
        ("ok.md", b"fine", "recordings"),
        ("big.txt", b"x" * (1024 * 1024 + 1), "knowledge"),
    ],
    ids=["format", "bad-json", "empty", "kind", "size"],
)
def test_an_upload_of_the_wrong_kind_format_or_size_is_refused(
    user, workspace, storage, auth_client, name, content, kind
):
    environment = _built(user, workspace, "actor-upload-bad")
    upload = auth_client.post(
        f"{BASE}/{environment.id}/files/",
        {"file": SimpleUploadedFile(name, content), "kind": kind},
        format="multipart",
    )
    assert upload.status_code == 400 and upload.json()["error"] in (
        "upload_invalid",
        "upload_kind_unknown",
    )
