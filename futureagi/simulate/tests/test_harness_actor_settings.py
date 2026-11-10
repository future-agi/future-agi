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
                "live_turns_per_actor": 6,
                "live_turns_per_trial": 12,
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
        "live_turns_per_actor": 6,
        "live_turns_per_trial": 12,
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
        {"max_active": 0.5},
        {"live_turns_per_actor": 2.5},
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


def _upload(auth_client, environment, name, content, **extra):
    return auth_client.post(
        f"{BASE}/{environment.id}/files/",
        {"file": SimpleUploadedFile(name, content), "kind": "knowledge", **extra},
        format="multipart",
    )


def test_an_uploaded_file_lands_in_its_kind_s_folder_whatever_path_its_name_carries(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-upload-paths")
    before = drafts.edit_sides(environment)["environment"]
    for name in ("../../scenario.json", "a/b.md"):
        assert (
            _upload(
                auth_client,
                environment,
                name,
                b'{"x": 1}' if name.endswith(".json") else b"text",
            ).status_code
            == 200
        )
    files = drafts.edit_sides(environment)["environment"]
    added = sorted(set(files) - set(before))
    assert added == ["knowledge/b.md", "knowledge/scenario.json"]
    assert all(files[path] == before[path] for path in before)
    # The service strips a path itself, whatever the transport did to the name first.
    from simulate.services.harness_actors import upload_environment_file

    upload_environment_file(
        environment,
        kind="knowledge",
        name="..\\..\\../notes/raw.md",
        content=b"text",
        user=user,
        expected_revision=None,
    )
    assert "knowledge/raw.md" in drafts.edit_sides(environment)["environment"]


def test_a_file_only_the_evaluator_reads_is_never_uploaded_for_an_actor(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-upload-evaluator")
    refused = _upload(auth_client, environment, "sub_goals.json", b"[]")
    assert (
        refused.status_code == 400
        and "may not be given a file by this name" in refused.json()["message"]
    )


def test_an_upload_at_a_stale_revision_is_refused_and_a_same_named_one_replaces_the_file(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "actor-upload-revision")
    assert (
        _upload(
            auth_client, environment, "policy.md", b"first", expected_revision=0
        ).status_code
        == 200
    )
    stale = _upload(
        auth_client, environment, "policy.md", b"stale", expected_revision=0
    )
    assert stale.status_code == 409 and stale.json()["error"] == "draft_changed"
    again = _upload(
        auth_client, environment, "policy.md", b"second", expected_revision=1
    )
    assert again.status_code == 200
    assert again.json()["knowledge_sources"]["files"].count("knowledge/policy.md") == 1
    assert (
        drafts.edit_sides(environment)["environment"]["knowledge/policy.md"]
        == b"second"
    )


def test_another_workspace_s_environment_takes_no_upload_and_no_limits_here(
    user, workspace, storage, auth_client
):
    from accounts.models.workspace import Workspace

    other = Workspace.objects.create(
        name="Other workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    environment = _built(user, other, "actor-upload-other-workspace")
    limits = auth_client.put(
        f"{BASE}/{environment.id}/actor-limits/", {"limits": {}}, format="json"
    )
    assert [
        _upload(auth_client, environment, "policy.md", b"x").status_code,
        limits.status_code,
    ] == [404, 404]


def test_a_limits_save_keeps_the_actors(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "actor-limits-keep")
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    saved = auth_client.put(
        f"{BASE}/{environment.id}/actor-limits/",
        {"limits": {"max_active": 2}, "expected_revision": 1},
        format="json",
    )
    assert saved.status_code == 200 and [
        one["id"] for one in saved.json()["actors"]
    ] == ["manager"]


def test_an_amend_to_a_scenario_whose_actor_the_list_no_longer_has_is_refused_with_the_reason(
    user, workspace, storage, auth_client, monkeypatch
):
    from simulate.services.harness_versioning.versions import suite_documents

    environment = _built(user, workspace, "actor-amend-unknown")
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    first, *rest = suite_documents(drafts.edit_sides(environment)["suite"])
    drafts.write_suite_edit(
        environment, [{**first, "actors": [{"id": "manager"}]}, *rest], user=user
    )
    monkeypatch.setattr(drafts, "actor_ids", lambda files: set())
    # An amend cannot set actors; editing another field of that scenario meets the guard.
    amend = auth_client.post(
        f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
        {
            "changes": [
                {
                    "op": "set_field",
                    "scenario": first["name"],
                    "field": "tests",
                    "value": "edited",
                }
            ]
        },
        format="json",
    )
    # An amend answers each change with a receipt: refused, with the reason.
    [receipt] = amend.json()["receipts"]
    assert amend.status_code == 200 and receipt["outcome"] == "refused"
    assert "manager is not an actor in this environment" in receipt["why"]


def test_an_amend_to_another_scenario_is_applied_while_one_names_an_actor_the_list_no_longer_has(
    user, workspace, storage, auth_client, monkeypatch
):
    from simulate.services.harness_versioning.versions import suite_documents

    environment = _built(user, workspace, "actor-amend-untouched")
    assert _add(auth_client, environment, MANAGER, revision=0).status_code == 200
    first, second, *rest = suite_documents(drafts.edit_sides(environment)["suite"])
    drafts.write_suite_edit(
        environment,
        [{**first, "actors": [{"id": "manager"}]}, second, *rest],
        user=user,
    )
    monkeypatch.setattr(drafts, "actor_ids", lambda files: set())
    amend = auth_client.post(
        f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
        {
            "changes": [
                {
                    "op": "set_field",
                    "scenario": second["name"],
                    "field": "tests",
                    "value": "edited",
                }
            ]
        },
        format="json",
    )
    [receipt] = amend.json()["receipts"]
    assert amend.status_code == 200 and receipt["outcome"] == "applied", receipt
    edited = {
        one["name"]: one
        for one in suite_documents(drafts.edit_sides(environment)["suite"])
    }
    assert edited[second["name"]]["tests"] == "edited"
    assert edited[first["name"]]["actors"] == [{"id": "manager"}]
