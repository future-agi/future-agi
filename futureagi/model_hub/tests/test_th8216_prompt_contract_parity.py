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
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.serializers.prompt_read_contracts import (
    PromptTemplatePageSerializer,
    PromptVersionHistoryPageSerializer,
)
from tfc.tests.openapi_parity import (
    assert_capture,
    assert_response_matches_contract,
    capture_record,
    load_swagger,
    to_json_schema,
    validation_errors,
)
from tfc.utils import api_contracts

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


def _shape(value):
    """Keep keys and JSON types; drop catalogue values that change with edits."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        shapes = {json.dumps(_shape(item), sort_keys=True) for item in value}
        return [json.loads(shape) for shape in sorted(shapes)]
    if value is None:
        return None
    return f"<{type(value).__name__}>"


CATALOGUE_VALUES = (
    "best_for",
    "use_case",
    "cutoff",
    "rate_limits",
    "latency",
    "pricing",
)


def _normalise_op035(body):
    return {**body, "result": _shape(body["result"])}


def _normalise_op036(body):
    # These keys are open JSON in the contract; keep only the top-level type.
    rows = [
        {
            **row,
            **{
                key: None if row[key] is None else f"<{type(row[key]).__name__}>"
                for key in CATALOGUE_VALUES
            },
        }
        for row in body["results"]
    ]
    return {**body, "results": rows}


def _row_shape(row):
    # Catalogue values are open JSON in the contract: keep only their top-level
    # type. Every other key keeps its full JSON shape.
    return {
        key: (None if value is None else f"<{type(value).__name__}>")
        if key in CATALOGUE_VALUES
        else _shape(value)
        for key, value in row.items()
    }


def _normalise_op036_search_page(body):
    # Which rows match, how many and in what order all follow the catalogue;
    # keep the page envelope and the distinct row shapes.
    shapes = {json.dumps(_row_shape(row), sort_keys=True) for row in body["results"]}
    return {
        **body,
        "count": _shape(body["count"]),
        "total_pages": _shape(body["total_pages"]),
        "next": _shape(body["next"]),
        "results": [json.loads(shape) for shape in sorted(shapes)],
    }


def _check_op035(swagger, auth_client):
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
        normalize=_normalise_op035,
        note="catalogue values reduced to their JSON types",
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


def _check_op036(swagger, auth_client):
    query = {"name": "gpt-4o-mini"}
    response = auth_client.get(MODELS_LIST, query)

    assert response.status_code == 200
    body = response.json()
    assert [row["model_name"] for row in body["results"]] == ["gpt-4o-mini"]
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
        normalize=_normalise_op036,
        note="one exact model; catalogue values reduced to their JSON types",
    )

    # The search branch with a multi-row page and a non-null next link.
    search = {"search": "gpt-4o-mini", "limit": 2}
    paged = auth_client.get(MODELS_LIST, search)

    assert paged.status_code == 200
    page = paged.json()
    assert len(page["results"]) == 2
    assert page["next"] is not None
    for row in page["results"]:
        assert not {"key", "api_key", "secret", "config_json"} & set(row)
    _check(
        swagger,
        "op036_models_list_search_page",
        "OP-036",
        "GET",
        MODELS_LIST,
        paged,
        request=search,
        normalize=_normalise_op036_search_page,
        note="search branch, two-row page with a next link; values reduced to JSON types",
    )


@pytest.mark.django_db
def test_op035_model_parameters_match_contract(swagger, auth_client, user):
    _check_op035(swagger, auth_client)


@pytest.mark.django_db
def test_op036_models_list_matches_contract(swagger, auth_client, user):
    _check_op036(swagger, auth_client)


def _edited_catalogue():
    """The repo catalogue after an unrelated edit: new prices, a new sibling."""
    from agentic_eval.core_evals.run_prompt import litellm_models

    catalogue = copy.deepcopy(litellm_models.AVAILABLE_MODELS)
    for model in catalogue:
        if model["model_name"].startswith("gpt-4o-mini"):
            model["pricing"] = {"input_per_1M_tokens": 0.123}
            model["latency"] = 4321
            model["use_case"] = ["edited use case"]
    sibling = next(m for m in catalogue if m["model_name"] == "gpt-4o-mini")
    catalogue.append({**sibling, "model_name": "gpt-4o-mini-th8216-sibling"})
    return catalogue


@pytest.mark.django_db
def test_op035_op036_captures_survive_catalogue_edits(swagger, auth_client, user):
    """C09: a catalogue edit must not read as contract drift."""
    from model_hub.views import run_prompt as run_prompt_views

    real_parameters = run_prompt_views.get_model_parameters

    def edited_parameters(*args):
        parameters = copy.deepcopy(real_parameters(*args))
        for slider in parameters.get("sliders", []):
            if slider.get("label") == "max_tokens":
                slider["max"] = 4096
        return parameters

    with (
        mock.patch(
            "agentic_eval.core_evals.run_prompt.litellm_models.AVAILABLE_MODELS",
            _edited_catalogue(),
        ),
        mock.patch.object(
            run_prompt_views, "get_model_parameters", side_effect=edited_parameters
        ),
    ):
        _check_op035(swagger, auth_client)
        _check_op036(swagger, auth_client)


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
        "name": True,
        "version": False,
        "label": False,
    }
    assert _query_parameters(swagger, TEMPLATE_LABELS) == {
        "template_id": False,
        "template_name": False,
    }


@pytest.mark.django_db
def test_op078_op059_blank_description_matches_contract(
    swagger, auth_client, organization, workspace, user
):
    """C02: PromptTemplate.description is blank=True, so "" is a stored value."""
    template = PromptTemplate.no_workspace_objects.create(
        name="TH8216 blank description",
        description="",
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names={},
    )
    _version(template, 1, is_default=True)

    detail = auth_client.get(f"/model-hub/prompt-templates/{template.id}/")
    by_name = auth_client.get(LABEL_BY_NAME, {"name": template.name, "version": "v1"})

    assert detail.status_code == 200 and by_name.status_code == 200
    assert detail.json()["description"] == ""
    assert by_name.json()["result"]["description"] == ""
    _check(
        swagger,
        "op078_template_detail_blank_description",
        "OP-078",
        "GET",
        TEMPLATE,
        detail,
    )
    _check(
        swagger,
        "op059_label_get_by_name_blank_description",
        "OP-059",
        "GET",
        LABEL_BY_NAME,
        by_name,
        request={"name": "<template>", "version": "v1"},
    )


@pytest.mark.django_db
def test_op099_foreign_template_reads_like_a_missing_one(swagger, auth_client, user):
    """D1: another organisation's template is indistinguishable from none."""
    foreign_org = Organization.objects.create(name="TH8216 foreign organisation")
    foreign = PromptTemplate.no_workspace_objects.create(
        name="TH8216 foreign prompt", organization=foreign_org, variable_names={}
    )
    _version(foreign, 1)

    response = auth_client.get(f"/model-hub/prompt-templates/{foreign.id}/versions/")
    missing = auth_client.get(
        "/model-hub/prompt-templates/00000000-0000-4000-8000-00000000dead/versions/"
    )

    assert response.status_code == missing.status_code == 500
    assert response.json() == missing.json()
    _check(
        swagger,
        "op099_versions_foreign_template",
        "OP-099",
        "GET",
        VERSIONS,
        response,
        note="legacy: a foreign template is the same 500 as a missing one",
    )


RESPONSE_DRIFT = "API response does not match declared serializer."


def _assert_debug_response_check_is_clean(
    settings, client, url, query, page_serializer
):
    """Run one read with DEBUG off and on: same queries, no drift warning.

    The DEBUG run must actually execute the response check against the
    declared page serializer, so the test cannot pass vacuously.
    """
    assert client.get(url, query).status_code == 200  # warm per-process caches
    settings.DEBUG = False
    with CaptureQueriesContext(connection) as plain:
        assert client.get(url, query).status_code == 200
    settings.DEBUG = True
    with (
        mock.patch("tfc.utils.api_contracts.logger") as contract_logger,
        mock.patch.object(
            api_contracts,
            "_validate_response",
            wraps=api_contracts._validate_response,
        ) as response_check,
        CaptureQueriesContext(connection) as checked,
    ):
        response = client.get(url, query)

    assert response.status_code == 200
    assert response_check.call_count == 1
    assert response_check.call_args.args[1] is page_serializer
    assert response_check.call_args.args[2].status_code == 200
    assert response.json()["results"] and response.json()["next"]
    drift = [
        call.kwargs.get("validation_errors")
        for call in contract_logger.warning.call_args_list
        if call.args and call.args[0] == RESPONSE_DRIFT
    ]
    assert drift == []
    assert [q["sql"] for q in checked.captured_queries] == [
        q["sql"] for q in plain.captured_queries
    ]


@pytest.mark.django_db
def test_debug_response_check_on_op099_reports_no_drift_and_adds_no_queries(
    settings, auth_client, history
):
    _assert_debug_response_check_is_clean(
        settings,
        auth_client,
        f"/model-hub/prompt-templates/{history.id}/versions/",
        {"limit": 2},
        PromptVersionHistoryPageSerializer,
    )


@pytest.mark.django_db
def test_debug_response_check_on_op068_reports_no_drift_and_adds_no_queries(
    settings, auth_client, organization, workspace, user
):
    folder = PromptFolder.no_workspace_objects.create(
        name="TH8216 folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    for name in ("TH8216 foldered prompt", "TH8216 second prompt"):
        template = _template(organization, workspace, user, name)
        template.prompt_folder = folder
        template.save(update_fields=["prompt_folder"])

    _assert_debug_response_check_is_clean(
        settings, auth_client, TEMPLATES, {"limit": 1}, PromptTemplatePageSerializer
    )


# M2: read definitions require every key the handler always emits. Keys that
# can legitimately be absent are listed here (and in REPORT.md).
OPTIONAL_READ_KEYS = {
    "PromptHistoryExecution": set(),
    "PromptTemplateDetailResponse": {"last_chunk_pos"},
    "PromptLabelledVersion": set(),
}


@pytest.mark.parametrize("definition", sorted(OPTIONAL_READ_KEYS))
def test_read_definitions_require_every_always_emitted_key(swagger, definition):
    schema = swagger["definitions"][definition]

    assert set(schema.get("required", [])) == (
        set(schema["properties"]) - OPTIONAL_READ_KEYS[definition]
    )


@pytest.mark.parametrize(
    "capture, row, definition, key",
    [
        (
            "op099_versions_default_page",
            ("results", 0),
            "PromptHistoryExecution",
            "labels",
        ),
        ("op078_template_detail", (), "PromptTemplateDetailResponse", "description"),
        ("op059_label_get_by_name", ("result",), "PromptLabelledVersion", "name"),
    ],
)
def test_dropping_an_always_emitted_key_fails_parity(
    swagger, capture, row, definition, key
):
    body = json.loads((CAPTURED / f"{capture}.json").read_text())["body"]
    for step in row:
        body = body[step]
    schema = {"$ref": f"#/definitions/{definition}"}
    assert validation_errors(swagger, schema, body) == []

    del body[key]

    assert validation_errors(swagger, schema, body) == [
        f"<root>: '{key}' is a required property"
    ]


def test_parity_harness_refuses_allof(swagger):
    """allOf plus the closed-object rule would mis-validate; fail loudly."""
    with pytest.raises(NotImplementedError, match="allOf"):
        to_json_schema(swagger, {"allOf": [{"type": "object", "properties": {}}]})


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
