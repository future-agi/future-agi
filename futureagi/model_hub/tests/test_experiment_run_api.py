import uuid

import pytest
from django.urls import reverse
from rest_framework import status

from accounts.models.user import OrgApiKey
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem


def _dataset_fixture(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="external-run-api-dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version, input={"case": value}, expected={"answer": value}
        )
        for value in (1, 2)
    ]
    return dataset, version, examples


def _create_body(version, examples, run_id=None):
    return {
        "run_id": str(run_id or uuid.uuid4()),
        "dataset_version_id": str(version.id),
        "expected_items": [
            {"example_id": str(example.example_id), "trial_number": trial}
            for example in examples
            for trial in (1, 2)
        ],
        "evaluator_metric_ids": [],
        "model_config": {"provider": "fixture", "model": "deterministic"},
        "metadata": {"code_revision": "fixture-v1"},
    }


@pytest.fixture
def external_api_fixture(organization, workspace, user):
    dataset, version, examples = _dataset_fixture(organization, workspace, user)
    body = _create_body(version, examples)
    return {"dataset": dataset, "version": version, "examples": examples, "body": body}


@pytest.mark.django_db
def test_create_external_run_returns_pins_and_replays_identically(
    auth_client, external_api_fixture
):
    body = external_api_fixture["body"]
    url = reverse("external-experiment-run-create")
    first = auth_client.post(url, body, format="json")
    assert first.status_code == status.HTTP_200_OK, first.json()
    result = first.json()["result"]
    assert result["task_kind"] == "external"
    assert result["status"] == "draft"
    assert result["dataset_version_id"] == body["dataset_version_id"]
    assert result["expected_items"] == sorted(
        body["expected_items"],
        key=lambda item: (item["example_id"], item["trial_number"]),
    )
    assert result["model_config"] == body["model_config"]
    assert result["metadata"] == body["metadata"]
    assert result["evaluator_bindings"] == []

    replay = auth_client.post(url, body, format="json")
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["result"] == result

    conflict = auth_client.post(
        url,
        {**body, "metadata": {"code_revision": "different"}},
        format="json",
    )
    assert conflict.status_code == status.HTTP_409_CONFLICT
    assert ExperimentRun.objects.filter(id=body["run_id"]).count() == 1


@pytest.mark.django_db
def test_create_rejects_scope_overrides_unknown_fields_and_large_plan(
    auth_client, external_api_fixture
):
    url = reverse("external-experiment-run-create")
    body = external_api_fixture["body"]
    for field in ("organization_id", "workspace_id", "task_kind"):
        response = auth_client.post(
            url, {**body, field: str(uuid.uuid4())}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()["details"]
    oversized = {
        **body,
        "run_id": str(uuid.uuid4()),
        "expected_items": body["expected_items"] * 5001,
    }
    response = auth_client.post(url, oversized, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_results_finalize_plan_and_identical_replay_is_a_noop(
    auth_client, external_api_fixture
):
    create = auth_client.post(
        reverse("external-experiment-run-create"),
        external_api_fixture["body"],
        format="json",
    )
    run_id = create.json()["result"]["id"]
    examples = external_api_fixture["examples"]
    items = [
        {
            "example_id": str(example.example_id),
            "trial_number": trial,
            "receipt_key": f"case-{index}",
            "status": "completed",
            "output": {"answer": str(example.input["case"])},
            "trace_id": f"trace-{index}",
            "root_span_id": f"span-{index}",
        }
        for index, (example, trial) in enumerate(
            pair for example in examples for pair in ((example, 1), (example, 2))
        )
    ]
    url = reverse("external-experiment-run-results", kwargs={"run_id": run_id})
    response = auth_client.post(url, {"items": items}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    assert result["run"]["status"] == "completed"
    assert len(result["items"]) == 4
    assert {item["trace_id"] for item in result["items"]} == {
        f"trace-{index}" for index in range(4)
    }

    replay = auth_client.post(url, {"items": items}, format="json")
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["result"]["run"]["status"] == "completed"
    assert (
        ExperimentRunItem.objects.filter(run_id=run_id, status="completed").count() == 4
    )


@pytest.mark.django_db
def test_results_batch_rolls_back_when_one_item_is_invalid(
    auth_client, external_api_fixture
):
    create = auth_client.post(
        reverse("external-experiment-run-create"),
        external_api_fixture["body"],
        format="json",
    )
    run_id = create.json()["result"]["id"]
    examples = external_api_fixture["examples"]
    items = [
        {
            "example_id": str(examples[0].example_id),
            "trial_number": 1,
            "receipt_key": "ok",
            "status": "completed",
            "output": {"ok": True},
        },
        {
            "example_id": str(uuid.uuid4()),
            "trial_number": 1,
            "receipt_key": "bad",
            "status": "completed",
            "output": {"bad": True},
        },
    ]
    response = auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": run_id}),
        {"items": items},
        format="json",
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert ExperimentRunItem.objects.filter(run_id=run_id).count() == 0
    assert ExperimentRun.objects.get(id=run_id).status == "draft"


@pytest.mark.django_db
def test_results_rejects_unpinned_score_evidence_and_malformed_trace(
    auth_client, external_api_fixture
):
    create = auth_client.post(
        reverse("external-experiment-run-create"),
        external_api_fixture["body"],
        format="json",
    )
    run_id = create.json()["result"]["id"]
    example = external_api_fixture["examples"][0]
    payload = {
        "items": [
            {
                "example_id": str(example.example_id),
                "trial_number": 1,
                "receipt_key": "bad-score",
                "status": "completed",
                "output": {},
                "score_refs": [
                    {
                        "metric_id": str(uuid.uuid4()),
                        "evaluator_version_id": str(uuid.uuid4()),
                        "status": "assessed",
                        "value": 0.5,
                    }
                ],
            }
        ]
    }
    response = auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": run_id}),
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert ExperimentRunItem.objects.filter(run_id=run_id).count() == 0


@pytest.mark.django_db
def test_forced_viewer_cannot_bypass_write_authorization(
    auth_client, external_api_fixture, user, workspace, organization
):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    response = auth_client.post(
        reverse("external-experiment-run-create"),
        external_api_fixture["body"],
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
@pytest.mark.parametrize("viewer", [False, True])
def test_actual_api_key_roles_govern_both_write_routes(
    external_api_fixture, user, workspace, organization, viewer
):
    from rest_framework.test import APIClient

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    key = OrgApiKey.no_workspace_objects.create(
        name="run-api-fixture",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
    )
    if viewer:
        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization
        ).update(level=Level.VIEWER, role="Viewer")
        WorkspaceMembership.no_workspace_objects.filter(
            user=user, workspace=workspace
        ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = client.post(
        reverse("external-experiment-run-create"),
        external_api_fixture["body"],
        format="json",
    )
    if viewer:
        assert response.status_code == 403
        assert not ExperimentRun.objects.filter(
            id=external_api_fixture["body"]["run_id"]
        ).exists()
        result_url = reverse(
            "external-experiment-run-results", kwargs={"run_id": uuid.uuid4()}
        )
        denied = client.post(
            result_url,
            {
                "items": [
                    {
                        "example_id": str(uuid.uuid4()),
                        "receipt_key": "x",
                        "status": "completed",
                    }
                ]
            },
            format="json",
        )
        assert denied.status_code == 403
    else:
        assert response.status_code == 200, response.json()
        header = response.json()["result"]
        example = external_api_fixture["examples"][0]
        result_url = reverse(
            "external-experiment-run-results", kwargs={"run_id": header["id"]}
        )
        written = client.post(
            result_url,
            {
                "items": [
                    {
                        "example_id": str(example.example_id),
                        "receipt_key": "x",
                        "status": "completed",
                        "output": "answer",
                    }
                ]
            },
            format="json",
        )
        assert written.status_code == 200, written.json()
        assert written.json()["result"]["run"]["status"] == "running"
        assert written.json()["result"]["items"][0]["example_id"] == str(
            example.example_id
        )


@pytest.mark.django_db
@pytest.mark.parametrize("hidden_by", ["organization", "workspace", "deleted"])
def test_both_write_routes_exclude_inaccessible_dataset(
    auth_client, external_api_fixture, organization, workspace, user, hidden_by
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from model_hub.services.experiment_run_creation import create_experiment_run

    fixture = external_api_fixture
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=fixture["version"],
        created_by=user,
        task_kind="external",
        expected_items=[(fixture["examples"][0].example_id, 1)],
    )
    if hidden_by == "organization":
        other = Organization.objects.create(name="other-ingestion-org")
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            organization=other
        )
    elif hidden_by == "workspace":
        other = Workspace.no_workspace_objects.create(
            name="other-ingestion-workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            workspace=other
        )
    else:
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            deleted=True
        )
    create = auth_client.post(
        reverse("external-experiment-run-create"), fixture["body"], format="json"
    )
    result = auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": run.id}),
        {
            "items": [
                {
                    "example_id": str(fixture["examples"][0].example_id),
                    "receipt_key": "scope",
                    "status": "completed",
                }
            ]
        },
        format="json",
    )
    assert create.status_code == result.status_code == 404
    assert ExperimentRunItem.no_workspace_objects.filter(run_id=run.id).count() == 0


@pytest.mark.django_db
def test_partial_and_failed_runs_replay_terminal_evidence_without_overwrite(
    auth_client, external_api_fixture
):
    fixture = external_api_fixture
    for target, statuses in (
        ("partial", ("completed", "failed")),
        ("failed", ("failed", "skipped")),
    ):
        body = {
            **fixture["body"],
            "run_id": str(uuid.uuid4()),
            "expected_items": fixture["body"]["expected_items"][:2],
        }
        create = auth_client.post(
            reverse("external-experiment-run-create"), body, format="json"
        )
        assert create.status_code == 200
        url = reverse(
            "external-experiment-run-results", kwargs={"run_id": body["run_id"]}
        )
        items = [
            {
                **identity,
                "receipt_key": f"{target}-{index}",
                "status": result_status,
                "error": {"message": "failed"} if result_status == "failed" else None,
            }
            for index, (identity, result_status) in enumerate(
                zip(body["expected_items"], statuses, strict=True)
            )
        ]
        result = auth_client.post(url, {"items": items}, format="json")
        assert result.status_code == 200, result.json()
        assert result.json()["result"]["run"]["status"] == target
        replay = auth_client.post(url, {"items": items}, format="json")
        assert replay.status_code == 200
        assert replay.json() == result.json()
        changed = [{**items[0], "receipt_key": "different"}]
        conflict = auth_client.post(url, {"items": changed}, format="json")
        assert conflict.status_code == 409
        assert ExperimentRun.objects.get(id=body["run_id"]).status == target


@pytest.mark.django_db
def test_pinned_scores_survive_default_change_and_compare_after_upload(
    auth_client, external_api_fixture, organization, workspace, user
):
    from model_hub.models.evals_metric import (
        EvalTemplate,
        EvalTemplateVersion,
        UserEvalMetric,
    )

    fixture = external_api_fixture
    template = EvalTemplate.no_workspace_objects.create(
        name="external-score",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    pin = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        config_snapshot={"threshold": 0.5},
        organization=organization,
        workspace=workspace,
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="external-score-binding",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=fixture["dataset"],
        user=user,
    )
    body = {
        **fixture["body"],
        "evaluator_metric_ids": [str(metric.id)],
        "expected_items": [fixture["body"]["expected_items"][0]],
    }
    first = auth_client.post(
        reverse("external-experiment-run-create"), body, format="json"
    )
    assert first.status_code == 200, first.json()
    binding = first.json()["result"]["evaluator_bindings"][0]
    assert binding["evaluator_version_id"] == str(pin.id)
    assert binding["config_snapshot"] == {"threshold": 0.5}
    pin.is_default = False
    pin.save(update_fields=["is_default"])
    EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=2,
        is_default=True,
        config_snapshot={"threshold": 0.9},
        organization=organization,
        workspace=workspace,
    )
    replay = auth_client.post(
        reverse("external-experiment-run-create"), body, format="json"
    )
    assert replay.json()["result"]["evaluator_bindings"] == [binding]
    url = reverse("external-experiment-run-results", kwargs={"run_id": body["run_id"]})
    ref = {
        "metric_id": str(metric.id),
        "evaluator_version_id": str(pin.id),
        "status": "assessed",
        "value": 0.8,
    }
    item = {
        **body["expected_items"][0],
        "receipt_key": "score",
        "status": "completed",
        "output": ["scalar-and-array", 1, None],
        "score_refs": [ref],
    }
    result = auth_client.post(url, {"items": [item]}, format="json")
    assert result.status_code == 200, result.json()
    assert (
        result.json()["result"]["items"][0]["score_refs"][0]["metric_id"]
        == ref["metric_id"]
    )
    assert (
        result.json()["result"]["items"][0]["score_refs"][0]["evaluator_version_id"]
        == ref["evaluator_version_id"]
    )
    assert result.json()["result"]["items"][0]["score_refs"][0]["status"] == "assessed"
    assert result.json()["result"]["items"][0]["score_refs"][0]["value"] == 0.8
    stored = ExperimentRunItem.objects.get(run_id=body["run_id"])
    assert stored.output == item["output"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "corruption",
    [
        "empty",
        "oversized",
        "duplicate",
        "unknown_item",
        "unknown_score",
        "negative_cost",
        "nan",
        "nul",
        "surrogate",
        "string_limit",
    ],
)
def test_malformed_batches_fail_before_any_write(
    auth_client, external_api_fixture, corruption
):
    fixture = external_api_fixture
    created = auth_client.post(
        reverse("external-experiment-run-create"), fixture["body"], format="json"
    )
    assert created.status_code == 200
    item = {
        **fixture["body"]["expected_items"][0],
        "receipt_key": "invalid",
        "status": "completed",
    }
    items = [item]
    if corruption == "empty":
        items = []
    elif corruption == "oversized":
        items = [item] * 101
    elif corruption == "duplicate":
        items = [item, item]
    elif corruption == "unknown_item":
        item["workspace_id"] = str(uuid.uuid4())
    elif corruption == "unknown_score":
        item["score_refs"] = [
            {
                "metric_id": str(uuid.uuid4()),
                "evaluator_version_id": str(uuid.uuid4()),
                "status": "unknown",
                "unexpected": True,
            }
        ]
    elif corruption == "negative_cost":
        item["cost"] = -1
    elif corruption == "nan":
        item["latency_ms"] = "NaN"
    elif corruption == "nul":
        item["trace_id"] = "trace\x00"
    elif corruption == "surrogate":
        item["output"] = "bad\ud800"
    else:
        item["output"] = "x" * 262145
    url = reverse(
        "external-experiment-run-results", kwargs={"run_id": fixture["body"]["run_id"]}
    )
    if corruption == "surrogate":
        import json

        response = auth_client.post(
            url, json.dumps({"items": items}), content_type="application/json"
        )
    else:
        response = auth_client.post(url, {"items": items}, format="json")
    assert response.status_code == 400, response.json()
    assert (
        ExperimentRunItem.objects.filter(run_id=fixture["body"]["run_id"]).count() == 0
    )
    assert ExperimentRun.objects.get(id=fixture["body"]["run_id"]).status == "draft"


@pytest.mark.django_db(transaction=True)
def test_concurrent_final_batches_serialize_and_complete_exact_plan(
    external_api_fixture, user, organization, workspace
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    from model_hub.services.experiment_run_creation import create_experiment_run
    from model_hub.services.experiment_run_ingestion import (
        ExternalRunResultInput,
        ingest_external_run_results,
    )

    fixture = external_api_fixture
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=fixture["version"],
        created_by=user,
        task_kind="external",
        expected_items=[(example.example_id, 1) for example in fixture["examples"]],
    )
    barrier = Barrier(2)

    def ingest(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            result = ingest_external_run_results(
                run_id=run.id,
                organization_id=organization.id,
                workspace_id=workspace.id,
                results=[
                    ExternalRunResultInput(
                        example_id=fixture["examples"][index].example_id,
                        trial_number=1,
                        receipt_key=f"thread-{index}",
                        status="completed",
                        output={"answer": index},
                    )
                ],
            )
            return result.run.status
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(ingest, (0, 1)))
    assert sorted(states) == ["completed", "running"]
    run.refresh_from_db()
    assert run.status == "completed"
    assert run.completed_at is not None
    assert run.items.count() == 2


@pytest.mark.django_db
def test_conflicting_retry_in_later_batch_rolls_back_new_case(
    auth_client, external_api_fixture
):
    fixture = external_api_fixture
    assert (
        auth_client.post(
            reverse("external-experiment-run-create"), fixture["body"], format="json"
        ).status_code
        == 200
    )
    url = reverse(
        "external-experiment-run-results", kwargs={"run_id": fixture["body"]["run_id"]}
    )
    first = {
        **fixture["body"]["expected_items"][0],
        "receipt_key": "first",
        "status": "completed",
        "output": 1,
    }
    assert auth_client.post(url, {"items": [first]}, format="json").status_code == 200
    second = {
        **fixture["body"]["expected_items"][1],
        "receipt_key": "second",
        "status": "completed",
        "output": 2,
    }
    failed = auth_client.post(
        url, {"items": [second, {**first, "output": 3}]}, format="json"
    )
    assert failed.status_code == 409
    assert (
        ExperimentRunItem.objects.filter(run_id=fixture["body"]["run_id"]).count() == 1
    )
    assert ExperimentRunItem.objects.get(run_id=fixture["body"]["run_id"]).output == 1
    successful = auth_client.post(url, {"items": [second]}, format="json")
    assert successful.status_code == 200
    assert (
        ExperimentRunItem.objects.filter(run_id=fixture["body"]["run_id"]).count() == 2
    )


@pytest.mark.django_db
def test_deleted_and_competing_running_claims_are_not_overwritten(
    auth_client, external_api_fixture
):
    from model_hub.services.experiment_run_writes import claim_run_item, transition_run

    fixture = external_api_fixture
    assert (
        auth_client.post(
            reverse("external-experiment-run-create"), fixture["body"], format="json"
        ).status_code
        == 200
    )
    run = ExperimentRun.objects.get(id=fixture["body"]["run_id"])
    scope = {
        "organization_id": fixture["dataset"].organization_id,
        "workspace_id": fixture["dataset"].workspace_id,
    }
    transition_run(run_id=run.id, status="queued", **scope)
    transition_run(run_id=run.id, status="running", **scope)
    identity = fixture["body"]["expected_items"][0]
    item = claim_run_item(run_id=run.id, **identity, receipt_key="owner", **scope)
    url = reverse("external-experiment-run-results", kwargs={"run_id": run.id})
    candidate = {**identity, "receipt_key": "other-owner", "status": "completed"}
    conflict = auth_client.post(url, {"items": [candidate]}, format="json")
    assert conflict.status_code == 409
    item.refresh_from_db()
    assert item.status == "running"
    item.deleted = True
    item.save(update_fields=["deleted"])
    deleted = auth_client.post(
        url, {"items": [{**candidate, "receipt_key": "owner"}]}, format="json"
    )
    assert deleted.status_code == 409
    assert "explicit recovery" in str(deleted.json())


def test_external_result_body_parser_enforces_inclusive_raw_bound():
    import io

    from rest_framework.exceptions import ParseError

    from model_hub.views.experiment_run_api import ExternalRunResultsJSONParser

    parser = ExternalRunResultsJSONParser()
    prefix = b'{"items":[]}'
    exact = prefix + b" " * (parser.max_body_bytes - len(prefix))
    assert parser.parse(io.BytesIO(exact)) == {"items": []}
    with pytest.raises(ParseError, match="at most 2097152 bytes"):
        parser.parse(io.BytesIO(exact + b" "))


def test_oversized_individual_result_is_a_request_validation_error():
    from model_hub.serializers.experiment_run_api import (
        ExternalRunResultsRequestSerializer,
    )

    ref = {
        "metric_id": str(uuid.uuid4()),
        "evaluator_version_id": str(uuid.uuid4()),
        "status": "unknown",
        "explanation": "x" * 16000,
    }
    serializer = ExternalRunResultsRequestSerializer(
        data={
            "items": [
                {
                    "example_id": str(uuid.uuid4()),
                    "receipt_key": "oversize",
                    "status": "completed",
                    "score_refs": [ref] * 70,
                }
            ]
        }
    )
    assert not serializer.is_valid()
    assert "1048576 bytes" in str(serializer.errors)
