"""Selected-result imports: authored first; execution belongs to the integrator."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from django.db import IntegrityError, connection
from django.urls import path, resolve
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator
from model_hub.models.choices import SourceChoices
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentRunItem
from model_hub.tests import test_results_launch_reads as reads
from rest_framework.test import APIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")]


@pytest.fixture
def world(organization, workspace, user, monkeypatch):
    monkeypatch.setattr(
        "model_hub.services.results_to_dataset.log_and_deduct_cost_for_resource_request", None
    )
    dataset, version, examples = reads.seed(organization, workspace, user, 3)
    run, binding = reads.pinned_run(version, examples, user, trials=2)
    for example in examples:
        reads.score_record(run, example, binding, 0, trial=1)
        reads.score_record(run, example, binding, 1, trial=2)
    target = Dataset.no_workspace_objects.create(
        name="Import target", organization=organization, workspace=workspace, user=user
    )
    columns = {
        name: Column.no_workspace_objects.create(
            dataset=target, name=name, source="OTHERS", data_type=kind
        ) for name, kind in (("Question", "text"), ("Reply", "json"), ("Score", "float"))
    }
    return SimpleNamespace(dataset=dataset, version=version, examples=examples,
                           run=run, binding=binding, target=target, columns=columns)


def payload(w, **changes):
    return {
        "source_dataset_id": str(w.dataset.id), "run_id": str(w.run.id),
        "example_ids": [str(e.example_id) for e in w.examples], "mode": "copy",
        "trial": 1, "split_ids": [], "new_splits": [], "expected_from": "keep",
        "version_description": "Selected results", "new_dataset_name": "Selected results",
        **changes,
    }


def post(client, w, target="new", **changes):
    return client.post(f"/model-hub/datasets/{target}/rows/from-results/",
                       payload(w, **changes), format="json")


def values(dataset_id):
    return [{c.column.name: c.value for c in Cell.no_workspace_objects.filter(
        row=row, deleted=False).select_related("column")}
        for row in Row.no_workspace_objects.filter(
            dataset_id=dataset_id, deleted=False).order_by("order")]


def test_routes_register_new_and_uuid_targets():
    for target, view_name in (("new", "NewResultsToDatasetView"),
                              (str(uuid4()), "ResultsToDatasetView")):
        match = resolve(f"/model-hub/datasets/{target}/rows/from-results/")
        assert match.func.view_class.__name__ == view_name


def test_new_dataset_copies_requested_order_full_output_and_zero_score(auth_client, world):
    w = world
    item = ExperimentRunItem.no_workspace_objects.get(
        run=w.run, example_id=w.examples[0].example_id, trial_number=1
    )
    item.output = {"reply": "x" * 10000, "false": False, "zero": 0}
    item.save(update_fields=["output"])
    ids = [str(w.examples[2].example_id), str(w.examples[0].example_id)]
    response = post(auth_client, w, example_ids=ids,
                    score_columns=[str(w.binding.metric.id)])
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["rows_requested"] == result["rows_added"] == 2
    assert result["rows_failed"] == 0 and result["failures"] == []
    assert [r["example_id"] for r in result["rows"]] == ids
    assert result["dataset_url"] == f"/dashboard/develop/{result['dataset_id']}?tab=data"
    copied = values(result["dataset_id"])
    assert json.loads(copied[0]["inputs"]) == w.examples[2].input
    assert json.loads(copied[1]["output"]) == item.output
    assert json.loads(copied[1][f"score:{w.binding.metric.id}"]) == 0
    assert Row.no_workspace_objects.filter(dataset=w.dataset).count() == 0
    assert w.dataset.dataset_versions.count() == 1


def test_existing_mapping_uses_selected_trial_and_leaves_other_rows(auth_client, world):
    w = world
    old = Row.no_workspace_objects.create(dataset=w.target, order=9)
    mapping = {"input:question": str(w.columns["Question"].id),
               "output": str(w.columns["Reply"].id),
               f"score:{w.binding.metric.id}": str(w.columns["Score"].id)}
    response = post(auth_client, w, str(w.target.id), column_map=mapping,
                    new_dataset_name="", trial=2)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["rows_added"] == 3
    assert Row.no_workspace_objects.get(id=old.id).order == 9
    assert list(Row.no_workspace_objects.filter(dataset=w.target).order_by("order")
                .values_list("order", flat=True)) == [9, 10, 11, 12]
    assert values(w.target.id)[1]["Score"] == "1"
    assert values(w.target.id)[1]["Question"] == w.examples[0].input["question"]
    row = Row.no_workspace_objects.get(id=result["rows"][0]["row_id"])
    assert row.metadata["result_import"]["trial"] == 2
    assert row.metadata["result_import"]["run_id"] == str(w.run.id)


def test_native_column_map_reads_snapshot_not_mutable_cells(auth_client, world):
    w = world
    source_column = Column.no_workspace_objects.create(
        dataset=w.dataset, name="Question", source="OTHERS", data_type="text"
    )
    source_row = Row.no_workspace_objects.create(dataset=w.dataset, order=0)
    Cell.no_workspace_objects.create(dataset=w.dataset, row=source_row,
                                    column=source_column, value="edited later")
    DatasetVersionItem.no_workspace_objects.filter(pk=w.examples[0].pk).update(
        input={"values": {str(source_column.id): "captured input"}},
        legacy_row=source_row,
    )
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    example_ids=[str(w.examples[0].example_id)],
                    column_map={str(source_column.id): str(w.columns["Question"].id),
                                "output": str(w.columns["Reply"].id)})
    assert response.status_code == 200, response.content
    assert values(w.target.id)[0]["Question"] == "captured input"
    source_row.deleted = True
    source_row.save(update_fields=["deleted"])
    response = post(auth_client, w, example_ids=[str(w.examples[0].example_id)])
    assert response.status_code == 422
    assert response.json()["result"]["failures"][0]["code"] == "example_unavailable"


def test_partial_result_accounts_for_deleted_unknown_and_missing_trials(auth_client, world):
    w = world
    DatasetVersionItem.no_workspace_objects.filter(pk=w.examples[1].pk).update(deleted=True)
    ExperimentRunItem.no_workspace_objects.filter(
        run=w.run, example_id=w.examples[2].example_id, trial_number=1).update(deleted=True)
    unknown = str(uuid4())
    response = post(auth_client, w, example_ids=[str(e.example_id) for e in w.examples] + [unknown])
    assert response.status_code == 207, response.content
    result = response.json()["result"]
    assert result["rows_requested"] == 4
    assert result["rows_added"] == 1 and result["rows_failed"] == 3
    assert [f["example_id"] for f in result["failures"]] == [
        str(w.examples[1].example_id), str(w.examples[2].example_id), unknown]
    assert [f["code"] for f in result["failures"]] == [
        "example_unavailable", "result_unavailable", "example_unavailable"]
    assert len(values(result["dataset_id"])) == 1


def test_all_failed_creates_no_empty_dataset_and_missing_score_is_not_zero(auth_client, world):
    w = world
    ExperimentRunItem.no_workspace_objects.filter(run=w.run).update(score_refs=[])
    before = Dataset.no_workspace_objects.count()
    response = post(auth_client, w, score_columns=[str(w.binding.metric.id)])
    assert response.status_code == 422, response.content
    body = response.json()
    assert body["status"] is False and body["code"] == "no_rows_added"
    assert body["result"]["dataset_id"] is None
    assert body["result"]["rows_added"] == 0
    assert {f["code"] for f in body["result"]["failures"]} == {"score_unavailable"}
    assert Dataset.no_workspace_objects.count() == before


@pytest.mark.parametrize("field,update", [
    ("source", "deleted"), ("target", "deleted"), ("run", "deleted"),
    ("version", "deleted"), ("source", "organization"), ("target", "organization"),
    ("source", "workspace"), ("target", "workspace"),
])
def test_inaccessible_resources_are_404_without_writes(auth_client, world, user, field, update):
    w = world
    obj = {"source": w.dataset, "target": w.target, "run": w.run, "version": w.version}[field]
    if update == "organization":
        value = Organization.objects.create(name="Other organization")
    elif update == "workspace":
        value = Workspace.no_workspace_objects.create(
            name="Other workspace", organization=user.organization, created_by=user)
    else:
        value = True
    type(obj).no_workspace_objects.filter(pk=obj.pk).update(**{update: value})
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"output": str(w.columns["Reply"].id)})
    assert response.status_code == 404, response.content
    assert not Row.no_workspace_objects.filter(dataset=w.target).exists()


def test_wrong_source_run_and_destination_column_are_refused(auth_client, world):
    w = world
    response = post(auth_client, w, source_dataset_id=str(w.target.id))
    assert response.status_code == 404
    foreign_column = Column.no_workspace_objects.create(
        dataset=w.dataset, name="Private", source="OTHERS", data_type="text")
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"output": str(foreign_column.id)})
    assert response.status_code == 400
    assert not Row.no_workspace_objects.filter(dataset=w.target).exists()


def test_viewer_and_unauthenticated_cannot_import(auth_client, world, user):
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        role=OrganizationRoles.MEMBER_VIEW_ONLY, level=Level.VIEWER)
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)
    user.organization_role = OrganizationRoles.MEMBER_VIEW_ONLY
    user.save(update_fields=["organization_role"])
    assert post(auth_client, world).status_code == 403
    assert post(APIClient(), world).status_code in (401, 403)
    assert not Dataset.no_workspace_objects.filter(name="Selected results").exists()


@pytest.mark.parametrize("change", [
    {"example_ids": []}, {"example_ids": ["not-a-uuid"]}, {"trial": 0},
    {"mode": "same_dataset"}, {"split_ids": [str(uuid4())]},
    {"new_splits": [{"name": "hard"}]}, {"save_version": True},
    {"unknown_field": True}, {"column_map": {}}, {"new_dataset_name": ""},
    {"score_columns": [str(uuid4())]},
])
def test_invalid_and_unsupported_requests_do_not_write(auth_client, world, change):
    response = post(auth_client, world, **change)
    assert response.status_code == 400, response.content
    assert not Dataset.no_workspace_objects.filter(name="Selected results").exists()


def test_duplicate_ids_names_and_mapping_targets_do_not_write(auth_client, world):
    w = world
    assert post(auth_client, w, example_ids=[str(w.examples[0].example_id)] * 2).status_code == 400
    assert post(auth_client, w, new_dataset_name=w.target.name).status_code == 400
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"inputs": str(w.columns["Reply"].id),
                                "output": str(w.columns["Reply"].id)})
    assert response.status_code == 400
    assert not Row.no_workspace_objects.filter(dataset=w.target).exists()


def test_value_and_target_schema_failures_are_reported_per_row(auth_client, world):
    w = world
    w.target.dataset_config = {"schema": {"enforce": "reject", "declared": {
        "type": "object", "properties": {"Question": {"enum": [w.examples[0].input["question"]]}}
    }}}
    w.target.save(update_fields=["dataset_config"])
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"input:question": str(w.columns["Question"].id)})
    assert response.status_code == 207, response.content
    assert response.json()["result"]["rows_added"] == 1
    assert {f["code"] for f in response.json()["result"]["failures"]} == {"invalid_value"}
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"output": str(w.columns["Score"].id)})
    assert response.status_code == 422


def test_database_failure_rolls_back_dataset_columns_rows_and_cells(auth_client, world, monkeypatch):
    before = Dataset.no_workspace_objects.count()
    manager_class = type(Cell.no_workspace_objects)
    original = manager_class.bulk_create

    def fail_cells(manager, objects, *args, **kwargs):
        if manager.model is Cell:
            raise IntegrityError("fixture storage failure")
        return original(manager, objects, *args, **kwargs)

    monkeypatch.setattr(manager_class, "bulk_create", fail_cells)
    response = post(auth_client, world)
    assert response.status_code == 503
    assert Dataset.no_workspace_objects.count() == before
    assert not Dataset.no_workspace_objects.filter(name="Selected results").exists()


def test_quota_refusal_writes_nothing(auth_client, world, monkeypatch):
    from tfc.constants.api_calls import APICallStatusChoices

    monkeypatch.setattr(
        "model_hub.services.results_to_dataset.log_and_deduct_cost_for_resource_request",
        lambda **kwargs: SimpleNamespace(status=APICallStatusChoices.RESOURCE_LIMIT.value),
    )
    assert post(auth_client, world).status_code == 429
    assert not Dataset.no_workspace_objects.filter(name="Selected results").exists()


def test_request_bound_and_response_serializers_are_explicit(world):
    from model_hub.serializers.results_to_dataset import (
        ResultsToDatasetRequestSerializer,
        ResultsToDatasetResponseSerializer,
    )

    serializer = ResultsToDatasetRequestSerializer(data=payload(
        world, example_ids=[str(uuid4()) for _ in range(1001)]))
    assert not serializer.is_valid()
    fields = ResultsToDatasetResponseSerializer().fields["result"].fields
    assert {"dataset_id", "dataset_url", "rows_requested", "rows_added", "rows_failed",
            "failures", "rows", "column_map", "trial"} <= set(fields)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("target_kind", ["new", "existing"])
@pytest.mark.parametrize("legacy_source", [False, True], ids=["default", "legacy-null"])
def test_default_workspace_dataset_locks_work_on_postgresql(
    auth_client, world, workspace, target_kind, legacy_source
):
    """The default/legacy workspace OR creates a nullable outer join on PostgreSQL."""
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL is required to regress nullable-join row locking.")
    assert workspace.is_default
    w = world
    if legacy_source:
        Dataset.no_workspace_objects.filter(pk=w.dataset.pk).update(workspace=None)
        type(w.version).no_workspace_objects.filter(pk=w.version.pk).update(workspace=None)
        # This legacy run has no evaluator pins requiring a non-null workspace.
        w.run = reads.run_for(w.version, w.examples, ordinal=2)
        for example in w.examples:
            reads.record(w.run, example)
    changes = {}
    target = "new"
    if target_kind == "existing":
        target = str(w.target.id)
        changes = {"new_dataset_name": "", "column_map": {"output": str(w.columns["Reply"].id)}}
    response = post(auth_client, w, target, **changes)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["rows_added"] == result["rows_requested"] == len(w.examples)
    assert result["rows_failed"] == 0
    assert result["created"] is (target_kind == "new")
    destination = Dataset.no_workspace_objects.get(pk=result["dataset_id"])
    assert destination.workspace_id == workspace.id


@pytest.mark.parametrize("target_kind", ["new", "existing"])
@pytest.mark.parametrize("raw,expected", [
    pytest.param("9007199254740993", 9007199254740993, id="integer-text"),
    pytest.param(9007199254740993, 9007199254740993, id="typed-integer"),
    pytest.param("9007199254740993.0", 9007199254740993, id="decimal-text"),
    pytest.param("9.007199254740993e15", 9007199254740993, id="exponent-text"),
    pytest.param(10 ** 400, 10 ** 400, id="integer-beyond-float-range"),
])
def test_native_integer_snapshot_is_copied_exactly(auth_client, world, target_kind, raw, expected):
    w = world
    source_column = Column.no_workspace_objects.create(
        dataset=w.dataset, name="Count", source="OTHERS", data_type="integer"
    )
    DatasetVersionItem.no_workspace_objects.filter(pk=w.examples[0].pk).update(
        input={"values": {str(source_column.id): raw}}
    )
    target = "new"
    changes = {"column_map": {str(source_column.id): "Count"}}
    if target_kind == "existing":
        destination = Column.no_workspace_objects.create(
            dataset=w.target, name="Count", source="OTHERS", data_type="integer"
        )
        target = str(w.target.id)
        changes = {"new_dataset_name": "", "column_map": {str(source_column.id): str(destination.id)}}
    response = post(auth_client, w, target,
                    example_ids=[str(w.examples[0].example_id)], **changes)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["rows_added"] == 1 and result["rows_failed"] == 0
    stored = values(result["dataset_id"])[0]["Count"]
    assert stored == str(expected)
    decoded = json.loads(stored)
    assert type(decoded) is int and decoded == expected


@pytest.mark.parametrize("expected", [
    pytest.param(9007199254740993, id="above-float-precision"),
    pytest.param(10 ** 400, id="beyond-float-range"),
])
@pytest.mark.parametrize("matches", [True, False], ids=["exact-enum", "neighbor-enum"])
def test_rejecting_schema_validates_exact_native_integers(auth_client, world, expected, matches):
    w = world
    source_column = Column.no_workspace_objects.create(
        dataset=w.dataset, name="Count", source="OTHERS", data_type="integer"
    )
    destination = Column.no_workspace_objects.create(
        dataset=w.target, name="Count", source="OTHERS", data_type="integer"
    )
    example_id = str(w.examples[0].example_id)
    DatasetVersionItem.no_workspace_objects.filter(pk=w.examples[0].pk).update(
        input={"values": {str(source_column.id): str(expected)}}
    )
    w.target.dataset_config = {"schema": {"enforce": "reject", "declared": {
        "type": "object", "properties": {
            "Count": {"type": "integer", "enum": [expected if matches else expected - 1]},
        }, "required": ["Count"],
    }}}
    w.target.save(update_fields=["dataset_config"])
    response = post(
        auth_client, w, str(w.target.id), new_dataset_name="", example_ids=[example_id],
        column_map={str(source_column.id): str(destination.id)},
    )
    assert response.status_code == (200 if matches else 422), response.content
    result = response.json()["result"]
    assert result["dataset_id"] == str(w.target.id)
    assert result["rows_requested"] == 1
    assert result["rows_added"] == int(matches)
    assert result["rows_failed"] == int(not matches)
    if matches:
        assert result["failures"] == []
        assert [row["example_id"] for row in result["rows"]] == [example_id]
        assert len(values(w.target.id)) == 1
        assert values(w.target.id)[0]["Count"] == str(expected)
    else:
        assert result["rows"] == []
        assert [(failure["example_id"], failure["code"]) for failure in result["failures"]] == [
            (example_id, "invalid_value"),
        ]
        assert not Row.no_workspace_objects.filter(dataset=w.target).exists()
        assert not Cell.no_workspace_objects.filter(dataset=w.target).exists()


@pytest.mark.parametrize("kind,raw,expected", [
    pytest.param("text", "", "", id="empty-text"),
    pytest.param("text", "false", "false", id="boolean-looking-text"),
    pytest.param("text", "001", "001", id="number-looking-text"),
    pytest.param("boolean", "False", False, id="boolean-text"),
    pytest.param("boolean", False, False, id="typed-boolean"),
    pytest.param("integer", "0", 0, id="integer-zero"),
    pytest.param("integer", None, None, id="null"),
    pytest.param("json", '{"n":9007199254740993,"flag":false}',
                 {"n": 9007199254740993, "flag": False}, id="json-text"),
    pytest.param("json", {"n": 9007199254740993, "flag": False},
                 {"n": 9007199254740993, "flag": False}, id="typed-json"),
    pytest.param("json", '""', "", id="json-empty-string"),
    pytest.param("float", "1.25", 1.25, id="float-text"),
])
def test_native_snapshot_decoding_preserves_value_types(auth_client, world, kind, raw, expected):
    w = world
    source_column = Column.no_workspace_objects.create(
        dataset=w.dataset, name="Input", source="OTHERS", data_type=kind
    )
    DatasetVersionItem.no_workspace_objects.filter(pk=w.examples[0].pk).update(
        input={"values": {str(source_column.id): raw}}
    )
    response = post(auth_client, w, example_ids=[str(w.examples[0].example_id)],
                    column_map={str(source_column.id): "Input"})
    assert response.status_code == 200, response.content
    stored = values(response.json()["result"]["dataset_id"])[0]["Input"]
    copied = None if stored is None else json.loads(stored)
    assert type(copied) is type(expected)
    assert copied == expected


@pytest.mark.parametrize("kind,raw", [
    pytest.param("integer", "1.5", id="fractional-integer"),
    pytest.param("integer", "NaN", id="nan-integer"),
    pytest.param("integer", "Infinity", id="infinite-integer"),
    pytest.param("integer", True, id="boolean-not-integer"),
    pytest.param("integer", "1e999999999", id="oversized-integer-exponent"),
    pytest.param("boolean", 1, id="integer-not-boolean"),
    pytest.param("json", "NaN", id="nan-json"),
    pytest.param("json", "{", id="malformed-json"),
    pytest.param("float", "Infinity", id="infinite-float"),
])
def test_invalid_native_snapshot_values_are_row_failures(auth_client, world, kind, raw):
    w = world
    source_column = Column.no_workspace_objects.create(
        dataset=w.dataset, name="Input", source="OTHERS", data_type=kind
    )
    DatasetVersionItem.no_workspace_objects.filter(pk=w.examples[0].pk).update(
        input={"values": {str(source_column.id): raw}}
    )
    response = post(auth_client, w, example_ids=[str(w.examples[0].example_id)],
                    column_map={str(source_column.id): "Input"})
    assert response.status_code == 422, response.content
    result = response.json()["result"]
    assert result["dataset_id"] is None and result["rows_added"] == 0
    assert result["failures"][0]["code"] == "invalid_value"
    assert not Dataset.no_workspace_objects.filter(name="Selected results").exists()


@pytest.mark.parametrize("target_kind", ["new", "existing"])
def test_invalid_native_json_snapshot_does_not_abort_valid_rows(auth_client, world, target_kind):
    w = world
    source_column = Column.no_workspace_objects.create(
        dataset=w.dataset, name="Input", source="OTHERS", data_type="json"
    )
    snapshots = [{"value": 0}, None, {"value": 9007199254740993}]
    for index, example in enumerate(w.examples):
        raw = "NaN" if index == 1 else json.dumps(snapshots[index])
        DatasetVersionItem.no_workspace_objects.filter(pk=example.pk).update(
            input={"values": {str(source_column.id): raw}}
        )
    target = "new"
    column_name = "Input"
    changes = {"column_map": {str(source_column.id): column_name}}
    if target_kind == "existing":
        target = str(w.target.id)
        column_name = "Reply"
        changes = {"new_dataset_name": "", "column_map": {
            str(source_column.id): str(w.columns[column_name].id),
        }}
    ids = [str(w.examples[index].example_id) for index in (1, 2, 0)]
    response = post(auth_client, w, target, example_ids=ids, **changes)
    assert response.status_code == 207, response.content
    body = response.json()
    assert body["status"] is True
    result = body["result"]
    assert result["outcome"] == "partial"
    assert result["created"] is (target_kind == "new")
    assert result["rows_requested"] == 3
    assert result["rows_added"] == 2 and result["rows_failed"] == 1
    assert [row["example_id"] for row in result["rows"]] == ids[1:]
    assert [(failure["example_id"], failure["code"]) for failure in result["failures"]] == [
        (ids[0], "invalid_value"),
    ]
    assert [json.loads(row[column_name]) for row in values(result["dataset_id"])] == [
        snapshots[2], snapshots[0],
    ]


@pytest.mark.parametrize("generated_name", ["Accuracy", "Question"], ids=["extra-property", "duplicate-name"])
def test_target_schema_excludes_generated_columns(auth_client, world, generated_name):
    w = world
    Column.no_workspace_objects.filter(pk__in=[w.columns["Reply"].pk, w.columns["Score"].pk]).update(
        deleted=True
    )
    generated = Column.no_workspace_objects.create(
        dataset=w.target, name=generated_name, source=SourceChoices.EVALUATION.value, data_type="float"
    )
    w.target.dataset_config = {"schema": {"enforce": "reject", "declared": {
        "type": "object", "properties": {"Question": {"type": "string"}},
        "required": ["Question"], "additionalProperties": False,
    }}}
    w.target.save(update_fields=["dataset_config"])
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"input:question": str(w.columns["Question"].id)})
    assert response.status_code == 200, response.content
    assert response.json()["result"]["rows_added"] == len(w.examples)
    assert values(w.target.id) == [{"Question": example.input["question"]} for example in w.examples]
    assert not Cell.no_workspace_objects.filter(dataset=w.target, column=generated).exists()
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"input:question": str(generated.id)})
    assert response.status_code == 400
    assert Row.no_workspace_objects.filter(dataset=w.target).count() == len(w.examples)


@pytest.mark.parametrize("duplicate_name", [False, True], ids=["unmapped-required", "duplicate-writable"])
def test_target_schema_still_checks_all_writable_columns(auth_client, world, duplicate_name):
    w = world
    if duplicate_name:
        Column.no_workspace_objects.create(
            dataset=w.target, name="Question", source="OTHERS", data_type="text"
        )
    w.target.dataset_config = {"schema": {"enforce": "reject", "declared": {
        "type": "object", "properties": {
            "Question": {"type": "string"}, "Reply": {"type": "object"}, "Score": {"type": "number"},
        }, "required": ["Reply"], "additionalProperties": False,
    }}}
    w.target.save(update_fields=["dataset_config"])
    response = post(auth_client, w, str(w.target.id), new_dataset_name="",
                    column_map={"input:question": str(w.columns["Question"].id)})
    assert response.status_code == (400 if duplicate_name else 422), response.content
    if not duplicate_name:
        assert {failure["code"] for failure in response.json()["result"]["failures"]} == {"invalid_value"}
    assert not Row.no_workspace_objects.filter(dataset=w.target).exists()


def test_result_import_openapi_operations_have_distinct_ids_and_path_parameters():
    new_match = resolve("/model-hub/datasets/new/rows/from-results/")
    existing_match = resolve(f"/model-hub/datasets/{uuid4()}/rows/from-results/")
    generator = OpenAPISchemaGenerator(
        info=openapi.Info(title="Result imports", default_version="v1"),
        patterns=[
            path("model-hub/datasets/new/rows/from-results/", new_match.func),
            path("model-hub/datasets/<uuid:dataset_id>/rows/from-results/", existing_match.func),
        ],
    )
    schema = generator.get_schema(request=None, public=True)
    operations = {}
    for path_item in schema.paths.values():
        operation = path_item["post"]
        assert operation.operation_id not in operations
        parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
        operations[operation.operation_id] = [p["name"] for p in parameters if p["in"] == "path"]
    assert operations == {
        "dataset_new_rows_from_results": [],
        "dataset_rows_from_results": ["dataset_id"],
    }
