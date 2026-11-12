"""Synthetic fixture: public target configuration is pinned, not runtime proof."""

from copy import deepcopy
from uuid import uuid4

import pytest

from model_hub.models.skills_hub import HubSkillLabel
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextOperation,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.tests.skills_hub import test_i2_integration as http
from model_hub.tests.skills_hub.r3_l04 import conftest as native
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as releases
from model_hub.tests.skills_hub.r3_l06 import conftest as contexts

admin = http.admin
context = contexts.context
native_configuration = native.native_configuration
context_version = contexts.context_version
actors = releases.actors
pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/model-hub/skills/contexts/"


def binding_body(configuration):
    return {
        "name": "Synthetic pinned application",
        "kind": "runtime",
        "selector": {"label": "production"},
        "update_mode": "new_thread",
        "profile": "synthetic",
        "profile_version": "1",
        "repository": None,
        "expected_report_interval_seconds": 60,
        "configuration": configuration,
    }


def create(admin, context, body, key=None):
    return admin.post(
        BASE + f"{context.pk}/bindings/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(key or uuid4()),
    )


def test_public_configuration_identity_and_replay(admin, context, native_configuration):
    key = uuid4()
    body = binding_body(native_configuration)
    response = create(admin, context, body, key)
    assert response.status_code == 201, response.data
    value = response.data["result"]
    assert value["configuration"] == native_configuration
    assert value["configuration_digest"] == canonical_digest(native_configuration)
    assert value["configuration_digest"] == canonical_digest(value["configuration"])
    assert value["revision"] == 1
    row = HubSkillContextBinding.objects.get(pk=value["id"])
    assert row.configuration == native_configuration
    assert row.configuration_digest == value["configuration_digest"]
    replay = create(admin, context, body, key)
    assert replay.status_code == 201, replay.data
    assert replay.data["result"] == value
    assert HubSkillContextBinding.objects.filter(context=context).count() == 1
    listed = admin.get(BASE + f"{context.pk}/bindings/")
    assert listed.status_code == 200, listed.data
    assert listed.data["result"]["results"] == [value]


def test_legacy_repository_replay_and_kind_change_clear_native_identity(
    admin, context, native_configuration
):
    body = binding_body(None)
    body.pop("configuration")
    body.update(
        kind="repository",
        update_mode="repository_lock",
        repository={
            "provider": "github",
            "repository_id": "synthetic",
            "default_branch": "main",
            "project_scope": "",
        },
    )
    key = uuid4()
    created = create(admin, context, body, key)
    assert created.status_code == 201, created.data
    operation = HubSkillContextOperation.objects.get(idempotency_key=key)
    assert operation.request_digest == canonical_digest(
        {"context_id": str(context.pk), "binding": body}
    )
    replay = create(admin, context, body, key)
    assert replay.status_code == 201, replay.data
    assert replay.data == created.data
    native_target = create(admin, context, binding_body(native_configuration)).data[
        "result"
    ]
    changed = admin.patch(
        BASE + f"bindings/{native_target['id']}/",
        {"revision": 1, "binding": body},
        format="json",
    )
    assert changed.status_code == 200, changed.data
    assert changed.data["result"]["configuration"] is None
    assert changed.data["result"]["configuration_digest"] == ""
    assert changed.data["result"]["revision"] == 2


def test_configuration_cas_aba_and_explicit_removal(
    admin, context, native_configuration
):
    body = binding_body(native_configuration)
    first = create(admin, context, body).data["result"]
    path = BASE + f"bindings/{first['id']}/"
    other = deepcopy(body)
    other["configuration"]["connection_revision"] = "2"
    changed = admin.patch(path, {"revision": 1, "binding": other}, format="json")
    assert changed.status_code == 200, changed.data
    assert changed.data["result"]["revision"] == 2
    assert (
        changed.data["result"]["configuration_digest"] != first["configuration_digest"]
    )
    stale = admin.patch(path, {"revision": 1, "binding": body}, format="json")
    assert stale.status_code == 409, stale.data
    restored = admin.patch(path, {"revision": 2, "binding": body}, format="json")
    assert restored.status_code == 200, restored.data
    assert (
        restored.data["result"]["configuration_digest"] == first["configuration_digest"]
    )
    assert restored.data["result"]["revision"] == 3
    legacy = {key: value for key, value in body.items() if key != "configuration"}
    preserved = admin.patch(path, {"revision": 3, "binding": legacy}, format="json")
    assert preserved.status_code == 200, preserved.data
    assert preserved.data["result"]["configuration"] == native_configuration
    cleared = admin.patch(
        path, {"revision": 4, "binding": binding_body(None)}, format="json"
    )
    assert cleared.status_code == 200, cleared.data
    assert cleared.data["result"]["configuration"] is None
    assert cleared.data["result"]["configuration_digest"] == ""


@pytest.mark.parametrize("restore", [False, True])
def test_public_target_change_fences_approved_packet(
    admin, actors, context, context_version, native_configuration, restore
):
    author, reviewer = actors
    baseline = context_version(context)
    candidate = context_version(context, body="Reviewed candidate instructions.")
    HubSkillLabel.objects.create(skill=context, name="production", version=baseline)
    body = binding_body(native_configuration)
    created = create(admin, context, body)
    assert created.status_code == 201, created.data
    binding = created.data["result"]
    target = {
        "binding_id": binding["id"],
        "environment_id": None,
        "revision": binding["revision"],
        "configuration_digest": binding["configuration_digest"],
    }
    preview = releases.preview(author, context, candidate, target=target)
    request = releases.submit(author, reviewer, preview)
    releases.approve(reviewer, request, preview)
    changed_body = deepcopy(body)
    changed_body["configuration"]["tools_digest"] = "sha256:" + "f" * 64
    path = BASE + f"bindings/{binding['id']}/"
    changed = admin.patch(path, {"revision": 1, "binding": changed_body}, format="json")
    assert changed.status_code == 200, changed.data
    if restore:
        restored = admin.patch(path, {"revision": 2, "binding": body}, format="json")
        assert restored.status_code == 200, restored.data
        assert (
            restored.data["result"]["configuration_digest"]
            == target["configuration_digest"]
        )
    with pytest.raises(SkillsHubError) as refused:
        releases.execute(reviewer, request, preview)
    assert refused.value.status == 409
    assert context.labels.get(name="production").version_id == baseline.pk
    assert not context.label_events.filter(to_version=candidate.number).exists()


@pytest.mark.parametrize(
    "mutation",
    ["forged-digest", "unknown-field", "incomplete", "unobservable", "repository"],
)
def test_configuration_rejects_unqualified_shape(
    admin, context, native_configuration, mutation
):
    body = binding_body(native_configuration)
    if mutation == "forged-digest":
        body["configuration_digest"] = "sha256:" + "f" * 64
    elif mutation == "unknown-field":
        body["configuration"]["secret"] = "must-not-be-persisted"
    elif mutation == "incomplete":
        body["configuration"].pop("checkpoint_digest")
    elif mutation == "unobservable":
        body["configuration"]["observable_boundaries"] = ["catalog"]
    else:
        body.update(
            kind="repository",
            update_mode="repository_lock",
            repository={
                "provider": "github",
                "repository_id": "synthetic",
                "default_branch": "main",
                "project_scope": "",
            },
        )
    response = create(admin, context, body)
    assert response.status_code in {400, 422}, response.data
    assert not HubSkillContextBinding.objects.filter(context=context).exists()
