"""SRV-3: read one experiment run and its recorded items through the real views."""

import json
import uuid

import pytest
from django.urls import reverse
from rest_framework import status

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun

# reverse() in a test body may be the session's first URLconf import; see
# urlconf_loaded_without_workspace_context in model_hub/tests/conftest.py.
pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")


def _fixture(organization, workspace, user, cases=3):
    dataset = Dataset.no_workspace_objects.create(
        name="run-read-dataset",
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
        for value in range(cases)
    ]
    return dataset, version, examples


def _metric(organization, workspace, user, dataset):
    from model_hub.models.evals_metric import (
        EvalTemplate,
        EvalTemplateVersion,
        UserEvalMetric,
    )

    template = EvalTemplate.no_workspace_objects.create(
        name=f"run-read-score-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    pin = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=organization,
        workspace=workspace,
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="run-read-binding",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        user=user,
    )
    return metric, pin


def _create(auth_client, version, examples, metric_ids=(), trials=(1,)):
    body = {
        "run_id": str(uuid.uuid4()),
        "dataset_version_id": str(version.id),
        "expected_items": [
            {"example_id": str(example.example_id), "trial_number": trial}
            for example in examples
            for trial in trials
        ],
        "evaluator_metric_ids": [str(value) for value in metric_ids],
        "model_config": {"provider": "fixture", "model": "deterministic"},
        "metadata": {"code_revision": "fixture-v1"},
    }
    response = auth_client.post(
        reverse("external-experiment-run-create"), body, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    return body, response.json()["result"]


def _ingest(auth_client, run_id, items):
    response = auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": run_id}),
        {"items": items},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"]


def _detail(client, run_id):
    return client.get(reverse("experiment-run-detail", kwargs={"run_id": run_id}))


def _items(client, run_id, **query):
    return client.get(reverse("experiment-run-items", kwargs={"run_id": run_id}), query)


@pytest.fixture
def read_fixture(organization, workspace, user):
    dataset, version, examples = _fixture(organization, workspace, user)
    return {"dataset": dataset, "version": version, "examples": examples}


@pytest.mark.django_db
def test_run_created_and_ingested_through_real_views_reads_back_identically(
    auth_client, read_fixture, organization, workspace, user
):
    fixture = read_fixture
    metric, pin = _metric(organization, workspace, user, fixture["dataset"])
    body, created = _create(
        auth_client, fixture["version"], fixture["examples"], metric_ids=[metric.id]
    )
    first, second, third = (item for item in body["expected_items"])
    ref = {
        "metric_id": str(metric.id),
        "evaluator_version_id": str(pin.id),
        "status": "assessed",
        "value": 0.75,
        "explanation": "three of four",
    }
    ingested = _ingest(
        auth_client,
        body["run_id"],
        [
            {
                **first,
                "receipt_key": "receipt-a",
                "status": "completed",
                "output": {"answer": "a"},
                "trace_id": "trace-a",
                "root_span_id": "span-a",
                "latency_ms": 12.5,
                "cost": 0.0003,
                "token_usage": {"input": 10, "output": 4},
                "score_refs": [ref],
            },
            {
                **second,
                "receipt_key": "receipt-b",
                "status": "failed",
                "error": {"type": "Timeout", "message": "took too long"},
            },
        ],
    )
    assert ingested["run"]["status"] == "running"

    detail = _detail(auth_client, body["run_id"])
    assert detail.status_code == status.HTTP_200_OK, detail.json()
    header = detail.json()["result"]
    # The creation header round-trips except the lifecycle the ingest moved.
    for key in (
        "id",
        "dataset_version_id",
        "experiment_id",
        "task_kind",
        "expected_items",
        "evaluator_bindings",
        "model_config",
        "metadata",
        "created_at",
    ):
        assert header[key] == created[key], key
    assert header["status"] == "running"
    assert header["started_at"] is not None
    assert header["plan_size"] == 3
    assert header["item_counts"] == {
        "pending": 0,
        "running": 0,
        "completed": 1,
        "failed": 1,
        "skipped": 0,
        "not_started": 1,
    }
    assert "_futureagi_run_write" not in header["metadata"]

    listed = _items(auth_client, body["run_id"])
    assert listed.status_code == status.HTTP_200_OK, listed.json()
    page = listed.json()["result"]
    assert page["run_id"] == body["run_id"]
    assert page["run_status"] == "running"
    assert page["total"] == 2 and page["has_more"] is False
    by_receipt = {item["receipt_key"]: item for item in page["items"]}
    assert set(by_receipt) == {"receipt-a", "receipt-b"}
    done = by_receipt["receipt-a"]
    assert done["example_id"] == first["example_id"]
    assert done["trial_number"] == 1
    assert done["status"] == "completed"
    assert done["output"] == {"answer": "a"} and done["output_truncated"] is False
    assert done["trace_id"] == "trace-a" and done["root_span_id"] == "span-a"
    assert done["latency_ms"] == 12.5 and done["cost"] == 0.0003
    assert done["token_usage"] == {"input": 10, "output": 4}
    assert done["score_refs"] == [{**ref, "trace_id": None, "span_id": None}]
    failed = by_receipt["receipt-b"]
    assert failed["error"] == {"type": "Timeout", "message": "took too long"}
    assert failed["output"] is None

    # Finishing the plan finalizes the run, and the read shows it.
    _ingest(
        auth_client,
        body["run_id"],
        [{**third, "receipt_key": "receipt-c", "status": "skipped"}],
    )
    final = _detail(auth_client, body["run_id"]).json()["result"]
    assert final["status"] == "partial"
    assert final["completed_at"] is not None
    assert final["item_counts"]["not_started"] == 0
    assert final["item_counts"]["skipped"] == 1


@pytest.mark.django_db
def test_items_page_in_plan_order_with_total_and_has_more(auth_client, read_fixture):
    fixture = read_fixture
    body, _ = _create(
        auth_client, fixture["version"], fixture["examples"], trials=(1, 2)
    )
    _ingest(
        auth_client,
        body["run_id"],
        [
            {**identity, "receipt_key": f"r-{index}", "status": "completed"}
            for index, identity in enumerate(body["expected_items"])
        ],
    )
    plan = sorted(
        (item["example_id"], item["trial_number"]) for item in body["expected_items"]
    )
    seen = []
    offset = 0
    while True:
        response = _items(auth_client, body["run_id"], limit=4, offset=offset)
        assert response.status_code == status.HTTP_200_OK, response.json()
        page = response.json()["result"]
        assert page["total"] == 6
        assert page["limit"] == 4 and page["offset"] == offset
        seen.extend(
            (item["example_id"], item["trial_number"]) for item in page["items"]
        )
        if not page["has_more"]:
            break
        offset += 4
    assert seen == plan
    assert offset == 4


@pytest.mark.django_db
def test_status_filter_selects_failed_and_skipped_for_a_rerun(
    auth_client, read_fixture
):
    fixture = read_fixture
    body, _ = _create(auth_client, fixture["version"], fixture["examples"])
    statuses = ("completed", "failed", "skipped")
    _ingest(
        auth_client,
        body["run_id"],
        [
            {
                **identity,
                "receipt_key": f"r-{result_status}",
                "status": result_status,
                "error": {"message": "x"} if result_status == "failed" else None,
            }
            for identity, result_status in zip(
                body["expected_items"], statuses, strict=True
            )
        ],
    )
    page = _items(auth_client, body["run_id"], status="failed,skipped").json()["result"]
    assert page["total"] == 2
    assert {item["status"] for item in page["items"]} == {"failed", "skipped"}
    only_done = _items(auth_client, body["run_id"], status="completed").json()["result"]
    assert [item["receipt_key"] for item in only_done["items"]] == ["r-completed"]

    bad = _items(auth_client, body["run_id"], status="failed,exploded")
    assert bad.status_code == status.HTTP_400_BAD_REQUEST
    unknown = _items(auth_client, body["run_id"], page_size=10)
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_large_output_is_a_flagged_prefix_and_page_budget_is_enforced(
    auth_client, read_fixture
):
    fixture = read_fixture
    body, _ = _create(auth_client, fixture["version"], fixture["examples"][:1])
    long_text = "\u00e9" * 3000
    _ingest(
        auth_client,
        body["run_id"],
        [
            {
                **body["expected_items"][0],
                "receipt_key": "big",
                "status": "completed",
                "output": {"text": long_text},
            }
        ],
    )
    small = _items(auth_client, body["run_id"], max_value_bytes=17).json()["result"]
    item = small["items"][0]
    assert item["output_truncated"] is True
    canonical = json.dumps({"text": long_text}, sort_keys=True, separators=(",", ":"))
    assert item["output_bytes"] == len(canonical.encode("utf-8"))
    assert isinstance(item["output"], str)
    assert len(item["output"].encode("utf-8")) <= 17
    assert item["output"].startswith('{"text":"')

    full = _items(auth_client, body["run_id"], max_value_bytes=262144, limit=16)
    assert full.status_code == status.HTTP_200_OK
    assert full.json()["result"]["items"][0]["output"] == {"text": long_text}
    over = _items(auth_client, body["run_id"], max_value_bytes=262144, limit=17)
    assert over.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hidden_by",
    [
        "organization",
        "workspace",
        "deleted",
        "missing",
        "dataset_deleted",
        "version_deleted",
    ],
)
def test_run_outside_the_caller_scope_is_404_on_both_reads(
    auth_client, read_fixture, organization, user, hidden_by
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    fixture = read_fixture
    body, _ = _create(auth_client, fixture["version"], fixture["examples"])
    run_id = body["run_id"]
    if hidden_by == "organization":
        other = Organization.objects.create(name="other-read-org")
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            organization=other
        )
    elif hidden_by == "workspace":
        other = Workspace.no_workspace_objects.create(
            name="other-read-workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            workspace=other
        )
    elif hidden_by == "deleted":
        ExperimentRun.no_workspace_objects.filter(id=run_id).update(deleted=True)
    elif hidden_by == "dataset_deleted":
        Dataset.no_workspace_objects.filter(id=fixture["dataset"].id).update(
            deleted=True
        )
    elif hidden_by == "version_deleted":
        DatasetVersion.no_workspace_objects.filter(id=fixture["version"].id).update(
            deleted=True
        )
    else:
        run_id = str(uuid.uuid4())
    assert _detail(auth_client, run_id).status_code == status.HTTP_404_NOT_FOUND
    assert _items(auth_client, run_id).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_workspace_viewer_and_api_key_can_read(
    auth_client, read_fixture, organization, workspace, user
):
    from rest_framework.test import APIClient

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import OrgApiKey
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    fixture = read_fixture
    body, _ = _create(auth_client, fixture["version"], fixture["examples"])
    key = OrgApiKey.no_workspace_objects.create(
        name="run-read-fixture",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
    )
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    detail = _detail(client, body["run_id"])
    assert detail.status_code == status.HTTP_200_OK, detail.json()
    assert detail.json()["result"]["id"] == body["run_id"]
    assert _items(client, body["run_id"]).status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_internal_score_ref_aliases_and_defaults_are_normalised(
    auth_client, read_fixture, organization, workspace, user
):
    """Internal runners store ``score``/``version_id``; reads return one shape."""
    from model_hub.models.experiments import ExperimentRunItem

    fixture = read_fixture
    metric, pin = _metric(organization, workspace, user, fixture["dataset"])
    body, _ = _create(
        auth_client,
        fixture["version"],
        fixture["examples"][:1],
        metric_ids=[metric.id],
    )
    _ingest(
        auth_client,
        body["run_id"],
        [{**body["expected_items"][0], "receipt_key": "r", "status": "completed"}],
    )
    ExperimentRunItem.no_workspace_objects.filter(run_id=body["run_id"]).update(
        score_refs=[
            {"metric_id": str(metric.id), "version_id": str(pin.id), "score": 0.25},
            {"metric_id": str(metric.id), "version_id": str(pin.id)},
            {
                "metric_id": str(metric.id),
                "evaluator_version_id": str(pin.id),
                "score": True,
                "status": "error",
            },
        ]
    )
    refs = _items(auth_client, body["run_id"]).json()["result"]["items"][0][
        "score_refs"
    ]
    base = {
        "metric_id": str(metric.id),
        "evaluator_version_id": str(pin.id),
        "explanation": None,
        "trace_id": None,
        "span_id": None,
    }
    assert refs == [
        {**base, "status": "assessed", "value": 0.25},
        # No value: the default status is not_assessed.
        {**base, "status": "not_assessed", "value": None},
        # A boolean is not a score; an explicit status is kept.
        {**base, "status": "error", "value": None},
    ]


@pytest.mark.django_db
def test_error_truncation_and_a_zero_byte_budget(auth_client, read_fixture):
    fixture = read_fixture
    body, _ = _create(auth_client, fixture["version"], fixture["examples"][:2])
    long_error = {"message": "x" * 500}
    _ingest(
        auth_client,
        body["run_id"],
        [
            {
                **body["expected_items"][0],
                "receipt_key": "failed",
                "status": "failed",
                "error": long_error,
            },
            {
                **body["expected_items"][1],
                "receipt_key": "done",
                "status": "completed",
                "output": {"answer": 1},
            },
        ],
    )
    canonical = json.dumps(long_error, sort_keys=True, separators=(",", ":"))
    items = {
        item["receipt_key"]: item
        for item in _items(auth_client, body["run_id"], max_value_bytes=20).json()[
            "result"
        ]["items"]
    }
    failed = items["failed"]
    assert failed["error_truncated"] is True
    assert failed["error"] == canonical[:20]
    assert failed["error_bytes"] == len(canonical.encode("utf-8"))
    assert failed["output"] is None and failed["output_truncated"] is False
    assert failed["output_bytes"] == 0

    zero = {
        item["receipt_key"]: item
        for item in _items(auth_client, body["run_id"], max_value_bytes=0).json()[
            "result"
        ]["items"]
    }
    # Every present value becomes an empty, flagged prefix with its full size;
    # absent values stay null and unflagged.
    assert zero["failed"]["error"] == "" and zero["failed"]["error_truncated"] is True
    assert zero["failed"]["error_bytes"] == len(canonical.encode("utf-8"))
    assert zero["done"]["output"] == "" and zero["done"]["output_truncated"] is True
    assert zero["done"]["output_bytes"] == len('{"answer":1}')
    assert zero["done"]["error"] is None and zero["done"]["error_truncated"] is False


@pytest.mark.django_db
def test_pending_and_running_items_are_counted_and_show_the_claim_receipt(
    auth_client, read_fixture, organization, workspace
):
    from model_hub.models.experiments import ExperimentRunItem
    from model_hub.services.experiment_run_writes import claim_run_item

    fixture = read_fixture
    body, _ = _create(auth_client, fixture["version"], fixture["examples"])
    first, second, third = body["expected_items"]
    _ingest(
        auth_client,
        body["run_id"],
        [{**first, "receipt_key": "done", "status": "completed"}],
    )
    for identity, receipt in ((second, "claim-b"), (third, "claim-c")):
        claim_run_item(
            run_id=body["run_id"],
            example_id=identity["example_id"],
            trial_number=identity["trial_number"],
            receipt_key=receipt,
            organization_id=organization.id,
            workspace_id=workspace.id,
        )
    ExperimentRunItem.no_workspace_objects.filter(
        run_id=body["run_id"], example_id=third["example_id"]
    ).update(status="pending")

    header = _detail(auth_client, body["run_id"]).json()["result"]
    assert header["item_counts"] == {
        "pending": 1,
        "running": 1,
        "completed": 1,
        "failed": 0,
        "skipped": 0,
        "not_started": 0,
    }
    running = _items(auth_client, body["run_id"], status="running").json()["result"]
    assert [item["receipt_key"] for item in running["items"]] == ["claim-b"]


@pytest.mark.django_db
def test_a_run_without_the_write_envelope_reads_as_an_empty_plan(
    auth_client, read_fixture
):
    """Review S5: a hand-written or legacy row reads back, it does not 500."""
    fixture = read_fixture
    body, _ = _create(auth_client, fixture["version"], fixture["examples"])
    ExperimentRun.no_workspace_objects.filter(id=body["run_id"]).update(
        metadata={"code_revision": "legacy"}
    )
    detail = _detail(auth_client, body["run_id"])
    assert detail.status_code == status.HTTP_200_OK, detail.json()
    header = detail.json()["result"]
    assert header["expected_items"] == []
    assert header["plan_size"] == 0
    assert header["item_counts"]["not_started"] == 0
    assert header["metadata"] == {"code_revision": "legacy"}
    assert _items(auth_client, body["run_id"]).status_code == status.HTTP_200_OK
