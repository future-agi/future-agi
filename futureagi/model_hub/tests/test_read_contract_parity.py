"""Actual read responses shared with static/frontend contract regression tests."""

import json
import os
from pathlib import Path

import pytest
from django.urls import reverse
from jsonschema import Draft7Validator

from mcp_server.tool_generation import _json_schema
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.experiments import ExperimentRunItem
from model_hub.tests import test_dataset_version_api, test_run_comparison_api

dataset_fixture = test_dataset_version_api.version_api_dataset
run_fixture = test_run_comparison_api.comparison_api_fixture


def _validate_and_capture(response, definition, name):
    assert response.status_code == 200
    root = Path(__file__).resolve().parents[3]
    contract = json.loads((root / "api_contracts/openapi/swagger.json").read_text())
    schema = _json_schema(
        {
            "$ref": f"#/definitions/{definition}",
            "definitions": contract["definitions"],
        }
    )
    data = response.json()
    Draft7Validator(schema, format_checker=Draft7Validator.FORMAT_CHECKER).validate(
        data
    )
    if os.getenv("FI_UPDATE_READ_CONTRACT_FIXTURES") == "1":
        (root / "frontend/src/api/contracts/__tests__/fixtures" / name).write_text(
            json.dumps(data, indent=2) + "\n"
        )
    return data["result"]


@pytest.mark.django_db
def test_actual_dataset_reads_match_json_and_blank_contract(
    auth_client, dataset_fixture
):
    version = DatasetVersion.objects.create(dataset=dataset_fixture)
    values = ["text", ["text", None], {"nested": None}, False, 42]
    for index, value in enumerate(values):
        DatasetVersionItem.objects.create(
            dataset_version=version,
            input=value,
            expected=None if index % 2 == 0 else value,
            metadata={"optional": None, "tags": ["eval", None]},
            external_id="" if index == 0 else None,
        )
    detail = auth_client.get(
        reverse(
            "dataset-version-detail",
            kwargs={
                "dataset_id": dataset_fixture.id,
                "version_id": version.id,
            },
        )
    )
    result = _validate_and_capture(
        detail, "DatasetVersionDetailResponse", "dataset-version-detail.json"
    )
    assert [item["input"] for item in result["items"]] == values
    assert result["items"][0]["expected"] is None
    assert all(
        item["source_trace_id"] == item["source_span_id"] == ""
        for item in result["items"]
    )
    assert result["version"]["content_hash"] == ""
    listing = auth_client.get(
        reverse("dataset-version-list", kwargs={"dataset_id": dataset_fixture.id})
    )
    assert _validate_and_capture(
        listing, "DatasetVersionListResponse", "dataset-version-list.json"
    )["versions"][0]["id"] == str(version.id)


@pytest.mark.django_db
@pytest.mark.parametrize("evidence", ["assessed", "unassessed", "missing"])
def test_actual_comparison_nullable_evidence_contract(
    auth_client, run_fixture, evidence
):
    fixture = run_fixture
    items = ExperimentRunItem.no_workspace_objects.filter(
        run_id__in=[fixture["baseline"].id, fixture["candidate"].id]
    )
    items.update(trace_id=None, root_span_id="")
    if evidence == "unassessed":
        items.update(score_refs=[])
        items.filter(
            run=fixture["candidate"], example_id=fixture["examples"][1].example_id
        ).delete()
    if evidence == "missing":
        items.delete()
    response = auth_client.post(
        reverse("experiment-run-compare"),
        {**fixture["scope"], "passing_score": None},
        format="json",
    )
    result = _validate_and_capture(
        response, "RunComparisonResponse", f"run-comparison-{evidence}.json"
    )
    if evidence == "missing":
        assert result["comparison"]["cases"] == []
        assert len(result["missing_by_side"]["baseline"]) == 2
        assert result["trace_refs"][0]["baseline"] is None
    if evidence == "unassessed":
        assert result["comparison"]["baseline"]["score_average"] is None
        assert result["candidate_counts"]["missing"] == 1
    assert result["comparison"]["baseline"]["pass_count"] is None
