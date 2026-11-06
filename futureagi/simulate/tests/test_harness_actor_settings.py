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


def test_a_scenario_naming_an_actor_the_list_no_longer_has_never_blocks_an_edit_to_another(
    user, workspace, storage, auth_client, monkeypatch
):
    from simulate.services.harness_versioning.versions import suite_documents
    from simulate.services.hosted_harness import HostedHarnessError

    environment = _built(user, workspace, "actor-guard-scope")
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    first, *rest = suite_documents(drafts.edit_sides(environment)["suite"])
    drafts.write_suite_edit(
        environment, [{**first, "actors": [{"id": "manager"}]}, *rest], user=user
    )
    saved = suite_documents(drafts.edit_sides(environment)["suite"])
    # The actor list no longer has the manager, as after a removal.
    monkeypatch.setattr(drafts, "actor_ids", lambda files: set())
    other = [saved[0], {**saved[1], "instruction": "Ask for the invoice again."}]
    assert drafts.write_suite_edit(environment, other, user=user) is not None
    with pytest.raises(HostedHarnessError) as refused:
        drafts.write_suite_edit(
            environment,
            [{**saved[0], "instruction": "Ask again."}, saved[1]],
            user=user,
        )
    assert refused.value.code == "actor_unknown"


def test_the_actor_models_offered_and_accepted_are_the_ones_the_environment_was_sealed_with(
    user, workspace, storage, auth_client
):
    from simulate.services import harness_actors
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests import test_harness_versioning_record as record_tests
    from simulate.tests.test_harness_actors import CRM_CONTRACT
    from simulate.tests.test_harness_versioning_record import _authoring

    rules = json.loads(harness_actors._RULES_BEFORE_DECLARED.read_text())
    rules["actor_models"] = ["gemini-4.0-flash"]
    environment = record_tests._environment(user, workspace, "actor-models-sealed")
    record_authoring(
        environment,
        _authoring(contract=CRM_CONTRACT, **{"actor_rules.json": json.dumps(rules)}),
        origin="authoring",
    )
    listed = auth_client.get(f"{BASE}/{environment.id}/actors/").json()
    assert listed["actor_models"] == ["gemini-4.0-flash"]

    def put(model):
        return auth_client.put(
            f"{BASE}/{environment.id}/actor-limits/",
            {"limits": {"model": model}},
            format="json",
        )

    assert put("gemini-3.8-flash").status_code == 400
    assert put("gemini-4.0-flash").status_code == 200
    # A bundle sealed before its release listed models offers the ones that release ran.
    earlier = _built(user, workspace, "actor-models-earlier")
    assert auth_client.get(f"{BASE}/{earlier.id}/actors/").json()[
        "actor_models"
    ] == list(harness_actors.ACTOR_MODELS)
