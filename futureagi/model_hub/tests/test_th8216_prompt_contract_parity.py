"""TH-8216 A: captured prompt/label/model read responses match swagger.json.

Every test builds real rows, calls the real endpoint through ``auth_client``
and (1) stores a sanitised capture under
``fixtures/contracts/th8216/captured/`` and (2) validates the live body
against the operation's declared schema. Hand-written future-evolution cases
live separately under ``fixtures/contracts/th8216/synthetic/``. Regenerate captures with
``TH8216_REGENERATE_CAPTURES=1`` (see ``tfc/tests/openapi_parity.py``).
"""

import copy
import json
from pathlib import Path

import pytest

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.tests.openapi_parity import (
    assert_capture,
    assert_response_matches_contract,
    capture_record,
    load_swagger,
    validation_errors,
)

CAPTURED = Path(__file__).parent / "fixtures" / "contracts" / "th8216" / "captured"

VERSIONS = "/model-hub/prompt-templates/{id}/versions/"
TEMPLATES = "/model-hub/prompt-templates/"
TEMPLATE = "/model-hub/prompt-templates/{id}/"
LABELS = "/model-hub/prompt-labels/"
LABEL = "/model-hub/prompt-labels/{id}/"
TEMPLATE_LABELS = "/model-hub/prompt-labels/template-labels/"
LABEL_BY_NAME = "/model-hub/prompt-labels/get-by-name/"
MODEL_PARAMETERS = "/model-hub/api/model_parameters/"
MODELS_LIST = "/model-hub/api/models_list/"


@pytest.fixture(scope="module")
def swagger():
    return load_swagger()


def _snapshot(text="Hello {{name}}", model="gpt-4o-mini"):
    return {
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": "Be brief."}]},
            {"role": "user", "content": [{"type": "text", "text": text}]},
        ],
        "configuration": {
            "model": model,
            "temperature": 0.2,
            "template_format": "mustache",
        },
    }


def _template(organization, workspace, user, name="TH8216 contract prompt"):
    return PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names={"name": ["Ada"]},
    )


def _version(template, number, **overrides):
    values = {
        "original_template": template,
        "template_version": f"v{number}",
        "prompt_config_snapshot": _snapshot(f"Version {number} {{{{name}}}}"),
        "variable_names": {"name": ["Ada"]},
        "is_draft": False,
        "commit_message": f"commit {number}",
    }
    values.update(overrides)
    return PromptVersion.no_workspace_objects.create(**values)


def _check(swagger, name, operation_id, method, path, response, **capture):
    assert_capture(
        CAPTURED / f"{name}.json",
        capture_record(operation_id, method, path, response, **capture),
    )
    assert_response_matches_contract(swagger, path, method, response)


@pytest.fixture
def history(organization, workspace, user):
    """Three versions: a labelled default, a legacy list snapshot, a draft."""
    template = _template(organization, workspace, user)
    label = PromptLabel.no_workspace_objects.create(
        name="th8216-staging",
        type=LabelTypeChoices.CUSTOM.value,
        organization=organization,
        workspace=workspace,
    )
    v1 = _version(template, 1, is_default=True, output=["first answer"])
    v1.labels.add(label)
    _version(
        template,
        2,
        prompt_config_snapshot=[_snapshot("Legacy list snapshot")],
        output=None,
        metadata=None,
        evaluation_configs=None,
        commit_message=None,
    )
    _version(template, 3, is_draft=True, output=[], commit_message="")
    return template


@pytest.mark.django_db
def test_op099_prompt_versions_page_matches_contract(swagger, auth_client, history):
    response = auth_client.get(f"/model-hub/prompt-templates/{history.id}/versions/")

    assert response.status_code == 200
    _check(swagger, "op099_versions_default_page", "OP-099", "GET", VERSIONS, response)


@pytest.mark.django_db
def test_op099_prompt_versions_middle_page_matches_contract(
    swagger, auth_client, history
):
    response = auth_client.get(
        f"/model-hub/prompt-templates/{history.id}/versions/",
        {"page": 2, "limit": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3 and body["current_page"] == 2
    assert body["next"] and body["previous"]
    _check(
        swagger,
        "op099_versions_middle_page",
        "OP-099",
        "GET",
        VERSIONS,
        response,
        request={"page": 2, "limit": 1},
    )


@pytest.mark.django_db
def test_op099_prompt_versions_out_of_range_page_matches_contract(
    swagger, auth_client, history
):
    response = auth_client.get(
        f"/model-hub/prompt-templates/{history.id}/versions/", {"page": 9}
    )

    # DRF raises NotFound for an out-of-range page; the handler's catch-all
    # turns it into a 500 envelope (compatibility decision, see REPORT.md).
    assert response.status_code == 500
    _check(
        swagger,
        "op099_versions_out_of_range_page",
        "OP-099",
        "GET",
        VERSIONS,
        response,
        request={"page": 9},
        note="legacy: out-of-range page is a 500, not a 404",
    )


@pytest.mark.django_db
def test_op099_prompt_versions_invalid_limit_matches_contract(
    swagger, auth_client, history
):
    response = auth_client.get(
        f"/model-hub/prompt-templates/{history.id}/versions/", {"limit": 0}
    )

    assert response.status_code == 400
    _check(
        swagger,
        "op099_versions_invalid_limit",
        "OP-099",
        "GET",
        VERSIONS,
        response,
        request={"limit": 0},
    )


@pytest.mark.django_db
def test_op099_prompt_versions_null_legacy_row_matches_contract(
    swagger, auth_client, organization, workspace, user
):
    template = _template(organization, workspace, user, "TH8216 null legacy row")
    _version(template, 1)
    _version(template, 2, variable_names=None)

    response = auth_client.get(f"/model-hub/prompt-templates/{template.id}/versions/")

    # A nullable variable_names column fails serialization (``None.copy()``)
    # and the catch-all turns the whole page into a 500 (compat decision).
    assert response.status_code == 500
    _check(
        swagger,
        "op099_versions_null_variable_names_row",
        "OP-099",
        "GET",
        VERSIONS,
        response,
        note="legacy: one null variable_names row fails the whole page",
    )


@pytest.mark.django_db
def test_op099_prompt_versions_unknown_template_matches_contract(
    swagger, auth_client, user
):
    response = auth_client.get(
        "/model-hub/prompt-templates/00000000-0000-4000-8000-00000000dead/versions/"
    )

    # get_object() raises Http404 inside the handler's catch-all -> 500.
    assert response.status_code == 500
    _check(
        swagger,
        "op099_versions_unknown_template",
        "OP-099",
        "GET",
        VERSIONS,
        response,
        note="legacy: missing or foreign template is a 500, not a 404",
    )


@pytest.mark.django_db
def test_op068_prompt_template_list_matches_contract(swagger, auth_client, history):
    response = auth_client.get(TEMPLATES, {"limit": 1})

    assert response.status_code == 200
    _check(
        swagger,
        "op068_templates_list",
        "OP-068",
        "GET",
        TEMPLATES,
        response,
        request={"limit": 1},
    )


@pytest.mark.django_db
def test_op078_prompt_template_detail_matches_contract(swagger, auth_client, history):
    response = auth_client.get(f"/model-hub/prompt-templates/{history.id}/")

    assert response.status_code == 200
    _check(swagger, "op078_template_detail", "OP-078", "GET", TEMPLATE, response)


@pytest.mark.django_db
def test_op055_op063_prompt_labels_match_contract(
    swagger, auth_client, organization, workspace
):
    label = PromptLabel.no_workspace_objects.create(
        name="th8216-label",
        type=LabelTypeChoices.CUSTOM.value,
        organization=organization,
        workspace=workspace,
        metadata={"color": "teal"},
    )
    # System labels are global rows: organization is null.
    assert (
        auth_client.post("/model-hub/prompt-labels/create-system-labels/").status_code
        == 200
    )

    list_response = auth_client.get(LABELS, {"limit": 50})
    detail_response = auth_client.get(f"/model-hub/prompt-labels/{label.id}/")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert None in {row["organization"] for row in list_response.json()["results"]}
    _check(
        swagger,
        "op055_labels_list",
        "OP-055",
        "GET",
        LABELS,
        list_response,
        request={"limit": 50},
        normalize=lambda body: {
            **body,
            "results": sorted(body["results"], key=lambda row: row["name"]),
        },
    )
    _check(swagger, "op063_label_detail", "OP-063", "GET", LABEL, detail_response)


@pytest.mark.django_db
def test_op062_op059_label_lookups_match_contract(swagger, auth_client, history):
    labels_response = auth_client.get(TEMPLATE_LABELS, {"template_id": str(history.id)})
    by_name_response = auth_client.get(
        LABEL_BY_NAME, {"name": history.name, "label": "th8216-staging"}
    )

    assert labels_response.status_code == 200
    assert by_name_response.status_code == 200
    _check(
        swagger,
        "op062_template_labels",
        "OP-062",
        "GET",
        TEMPLATE_LABELS,
        labels_response,
        request={"template_id": "<template>"},
    )
    _check(
        swagger,
        "op059_label_get_by_name",
        "OP-059",
        "GET",
        LABEL_BY_NAME,
        by_name_response,
        request={"name": "<template>", "label": "th8216-staging"},
    )


@pytest.mark.django_db
def test_op035_model_parameters_match_contract(swagger, auth_client, user):
    query = {"model": "gpt-4o-mini", "provider": "openai", "model_type": "llm"}
    response = auth_client.get(MODEL_PARAMETERS, query)
    missing = auth_client.get(MODEL_PARAMETERS, {"model": "gpt-4o-mini"})

    assert response.status_code == 200
    assert missing.status_code == 400
    _check(
        swagger,
        "op035_model_parameters",
        "OP-035",
        "GET",
        MODEL_PARAMETERS,
        response,
        request=query,
    )
    _check(
        swagger,
        "op035_model_parameters_missing_query",
        "OP-035",
        "GET",
        MODEL_PARAMETERS,
        missing,
        request={"model": "gpt-4o-mini"},
    )


@pytest.mark.django_db
def test_op036_models_list_matches_contract(swagger, auth_client, user):
    query = {"search": "gpt-4o-mini", "limit": 2}
    response = auth_client.get(MODELS_LIST, query)

    assert response.status_code == 200
    body = response.json()
    assert body["results"], "the model catalogue should contain gpt-4o-mini"
    for row in body["results"]:
        assert not {"key", "api_key", "secret", "config_json"} & set(row)
    _check(
        swagger,
        "op036_models_list",
        "OP-036",
        "GET",
        MODELS_LIST,
        response,
        request=query,
    )


def _query_parameters(swagger, path):
    return {
        parameter["name"]: parameter.get("required", False)
        for parameter in swagger["paths"][path]["get"].get("parameters", [])
        if parameter.get("in") == "query"
    }


def test_op035_op036_declare_their_real_query_parameters(swagger):
    assert _query_parameters(swagger, MODEL_PARAMETERS) == {
        "model": True,
        "provider": True,
        "model_type": True,
    }
    assert _query_parameters(swagger, MODELS_LIST) == {
        "name": False,
        "search": False,
        "model_type": False,
        "exclude_providers": False,
        "page": False,
        "limit": False,
    }


def test_label_lookups_declare_their_real_query_parameters(swagger):
    assert _query_parameters(swagger, LABEL_BY_NAME) == {
        "name": False,
        "version": False,
        "label": False,
    }
    assert _query_parameters(swagger, TEMPLATE_LABELS) == {
        "template_id": False,
        "template_name": False,
    }


SYNTHETIC = CAPTURED.parent / "synthetic"


def _evolve(row, case_id):
    row = copy.deepcopy(row)
    if case_id == "unknown-provider-key-in-open-snapshot":
        row["prompt_config_snapshot"]["configuration"]["model_detail"][
            "reasoning_tiers"
        ] = ["low", "high"]
    elif case_id == "null-snapshot-legacy-row":
        row["prompt_config_snapshot"] = None
    elif case_id == "future-label-type":
        row["labels"] = [
            {"id": row["id"], "name": "experiment-a", "type": "experiment"}
        ]
    elif case_id == "new-top-level-field":
        row["archived_at"] = None
    elif case_id == "string-output":
        row["output"] = "legacy text output"
    else:
        raise AssertionError(f"unknown synthetic case {case_id}")
    return row


def test_op099_synthetic_evolution_cases_match_declared_openness(swagger):
    """Synthetic (not captured) rows: which future shapes the contract admits."""
    spec = json.loads((SYNTHETIC / "op099_history_evolution.json").read_text())
    captured = json.loads((CAPTURED / "op099_versions_default_page.json").read_text())
    base_row = captured["body"]["results"][0]
    schema = {"$ref": "#/definitions/PromptHistoryExecution"}

    assert spec["label"] == "synthetic"
    for case in spec["cases"]:
        errors = validation_errors(swagger, schema, _evolve(base_row, case["id"]))
        assert (not errors) is case["declared"], (case["id"], errors)
